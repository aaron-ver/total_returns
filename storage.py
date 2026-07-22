"""
Optional S3 artifact sync. LOCAL-DEFAULT: if LINKERS_S3_BUCKET is unset (or boto3 isn't installed)
every call is a no-op, so the pipeline runs exactly as before. When configured, it pushes the
consumable outputs (marts, exports, plots, dashboards) to S3 so the team shares one copy.

Auth: standard boto3 credential chain — env vars, ~/.aws/credentials, or AWS_PROFILE. NO secrets in
code. Config via environment:
  LINKERS_S3_BUCKET   target bucket, e.g. verition-linkers-rates      [required to enable]
  LINKERS_S3_PREFIX   optional key prefix (default: none)
  AWS_REGION          bucket region, e.g. us-east-2                    [recommended]
  (+ your AWS creds — set them via the access-portal copy-paste or an SSO profile)

Setup once:  pip install boto3   (into .venv)
Usage:       python storage.py            # push consumable ARTIFACTS to S3 (marts/exports/plots/dashboards/PDFs)
             python storage.py portal     # ONE shareable link: index page covering both dashboards + research PDFs
             python storage.py url [us]   # single-item link (intl default; us/tips for the US dashboard)
             python storage.py identity   # AWS auth diagnostic (no secrets printed)
             python storage.py push-raw   # TERMINAL box: upload raw caches (cache/, cache_intl/) after the pull
             python storage.py pull-raw   # CLOUD/any box: download raw caches so BUILD can run without a terminal
"""
from __future__ import annotations
import os, sys, glob, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = os.environ.get("LINKERS_S3_BUCKET")
PREFIX = os.environ.get("LINKERS_S3_PREFIX", "").strip("/")

# local dir -> S3 sub-prefix. Consumable outputs only (raw caches are regenerable — not synced).
DIRS = {"marts": "marts", "exports": "exports", "plots": "plots"}
FILES = {"dashboard_intl.html": "dashboards/dashboard_intl.html",
         "dashboard.html": "dashboards/dashboard.html",
         "eventstudio.html": "dashboards/eventstudio.html",
         "eventflow.html": "dashboards/eventflow.html"}

# The RAW pulled caches. Pushed by the TERMINAL box right after the Bloomberg pull; pulled by a
# headless/cloud box (or any teammate) before BUILD — so the compile can run anywhere, not just
# where Bloomberg is. This is what makes the data live in S3 instead of one machine.
RAW_DIRS = {"cache": "raw/cache", "cache_intl": "raw/cache_intl"}


def _research_pdfs():
    """Local research PDFs (experiments/out/*.pdf) -> S3 keys under research/."""
    d = os.path.join(HERE, "experiments", "out")
    if not os.path.isdir(d):
        return {}
    return {os.path.join(d, f): f"research/{f}" for f in sorted(os.listdir(d))
            if f.endswith(".pdf")}


def enabled():
    if not BUCKET:
        return False
    try:
        import boto3  # noqa: F401
        return True
    except Exception:
        return False


def _client():
    import boto3
    return boto3.client("s3")


def _key(*parts):
    return "/".join(p for p in ([PREFIX] + [x for x in parts if x]) if p)


def _put_if_changed(cli, path, key, extra=None):
    """Upload only when the object is missing or its content differs (size + MD5 vs the S3 ETag),
    so a daily sync re-sends just the handful of files that actually changed. Returns True if it
    uploaded, False if it skipped an identical object."""
    try:
        h = cli.head_object(Bucket=BUCKET, Key=key)
        etag = h.get("ETag", "").strip('"')
        if h.get("ContentLength") == os.path.getsize(path) and "-" not in etag:
            with open(path, "rb") as fh:
                if hashlib.md5(fh.read()).hexdigest() == etag:
                    return False  # byte-identical already in S3 -> skip
    except Exception:
        pass  # not present (or HEAD failed) -> upload
    cli.upload_file(path, BUCKET, key, ExtraArgs=extra or {})
    return True


def push():
    if not enabled():
        print("  [storage] S3 not configured (set LINKERS_S3_BUCKET + AWS creds, pip install boto3) — skipped")
        return 0
    cli = _client(); up = skip = 0
    print(f"  [storage] sync -> s3://{BUCKET}/{PREFIX or ''}  (incremental: only changed files)")
    for d, sub in DIRS.items():
        base = os.path.join(HERE, d)
        if not os.path.isdir(base):
            continue
        u = s = 0
        for path in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if os.path.isfile(path):
                rel = os.path.relpath(path, base).replace("\\", "/")
                if _put_if_changed(cli, path, _key(sub, rel)): u += 1
                else: s += 1
        up += u; skip += s; print(f"    {d:10s} -> {sub}/  ({u} changed, {s} unchanged)")
    singles = {os.path.join(HERE, f): key for f, key in FILES.items()}
    singles.update(_research_pdfs())                       # experiment PDFs -> research/
    for p, key in singles.items():
        if os.path.isfile(p):
            # no-cache => the browser revalidates every visit, so a shared link always shows the
            # latest daily build instead of a stale cached copy.
            extra = ({"ContentType": "text/html", "CacheControl": "no-cache"} if p.endswith(".html")
                     else {"ContentType": "application/pdf", "CacheControl": "no-cache"}
                     if p.endswith(".pdf") else {})
            if _put_if_changed(cli, p, _key(key), extra):
                up += 1; print(f"    {os.path.basename(p)} -> {key}  (changed)")
            else:
                skip += 1; print(f"    {os.path.basename(p)} -> {key}  (unchanged)")
    print(f"  [storage] uploaded {up} changed, skipped {skip} unchanged")
    return up


def identity():
    """Diagnose AWS auth WITHOUT printing secrets: how boto3 resolved credentials, whether they're
    TEMPORARY (expire) or long-lived, which identity/role you are, and whether the bucket is
    reachable + writable. Run this to answer 'can my IAM role be used for the scheduled push?'."""
    print(f"  config: bucket={BUCKET!r}  region={os.environ.get('AWS_REGION')!r}  prefix={PREFIX!r}")
    try:
        import boto3
    except Exception as e:
        print("  boto3 not installed into this venv:", e); return
    sess = boto3.Session()
    creds = sess.get_credentials()
    if not creds:
        print("  NO CREDENTIALS resolved — no [default] in ~/.aws/credentials, no attached role, no env vars.")
        print("  -> on a laptop you need a long-lived IAM key; on an EC2 box you need the role attached.")
        return
    fc = creds.get_frozen_credentials()
    method = getattr(creds, "method", "?")
    temp = fc.token is not None
    print(f"  resolved via: {method}")
    print(f"  credential type: {'TEMPORARY — has a session token, so it EXPIRES' if temp else 'LONG-LIVED — no session token, does not expire'}")
    # 'iam-role' = EC2/instance-profile (auto, no expiry); 'assume-role' = assumed via config;
    # 'sso' = SSO cache; 'shared-credentials-file'/'env' = keys you placed (temp if token present).
    from botocore.config import Config
    cfg = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})  # fail fast, don't hang
    try:
        who = boto3.client("sts", config=cfg).get_caller_identity()
        print(f"  identity ARN: {who.get('Arn')}")
    except Exception as e:
        print(f"  sts.get_caller_identity FAILED: {type(e).__name__}: {str(e)[:140]}")
    if BUCKET:
        try:
            boto3.client("s3", config=cfg).head_bucket(Bucket=BUCKET)
            print(f"  s3 reach: OK  (can see s3://{BUCKET})")
        except Exception as e:
            print(f"  s3 reach: FAILED  {type(e).__name__}: {str(e)[:140]}")


def push_raw():
    """Upload the RAW pulled caches (cache/, cache_intl/) to S3. The terminal box runs this right
    after the Bloomberg pull, so a headless build elsewhere can source the same data."""
    if not enabled():
        print("  [storage] S3 not configured — skipped"); return 0
    cli = _client(); up = skip = 0
    print(f"  [storage] push RAW -> s3://{BUCKET}/{_key('raw') or 'raw'}  (incremental: only changed files)")
    for d, sub in RAW_DIRS.items():
        base = os.path.join(HERE, d)
        if not os.path.isdir(base):
            continue
        u = s = 0
        for path in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if os.path.isfile(path):
                rel = os.path.relpath(path, base).replace("\\", "/")
                if _put_if_changed(cli, path, _key(sub, rel)): u += 1
                else: s += 1
        up += u; skip += s; print(f"    {d:10s} -> {sub}/  ({u} changed, {s} unchanged)")
    print(f"  [storage] uploaded {up} changed, skipped {skip} unchanged raw files")
    return up


def pull_raw():
    """Download the RAW caches from S3 into local cache/ dirs, so a build can run WITHOUT a
    Bloomberg terminal (cloud cron, or any teammate's machine). Mirrors push_raw()."""
    if not enabled():
        print("  [storage] S3 not configured — skipped"); return 0
    cli = _client(); total = 0
    print(f"  [storage] pull RAW <- s3://{BUCKET}/{_key('raw') or 'raw'}")
    paginator = cli.get_paginator("list_objects_v2")
    for d, sub in RAW_DIRS.items():
        prefix = _key(sub) + "/"
        n = 0
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(prefix):]
                if not rel:
                    continue
                dest = os.path.join(HERE, d, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                cli.download_file(BUCKET, obj["Key"], dest); n += 1
        total += n; print(f"    {sub}/ -> {d}  ({n} files)")
    print(f"  [storage] downloaded {total} raw files")
    return total


def url(key=None, days=7):
    """Print a temporary browser link to a dashboard that RENDERS in-browser (no download). Valid up
    to 7 days (the IAM-user max). This is the zero-infra way to share the private dashboard today;
    for a permanent stable internal URL, front the bucket with CloudFront (cloud-team / Terraform).
      python storage.py url          # intl linkers dashboard
      python storage.py url us       # US TIPS dashboard
      python storage.py url <s3key>  # anything else in the bucket"""
    if not enabled():
        print("  [storage] S3 not configured — skipped"); return None
    shortcuts = {None: "dashboards/dashboard_intl.html", "intl": "dashboards/dashboard_intl.html",
                 "us": "dashboards/dashboard.html", "tips": "dashboards/dashboard.html"}
    k = _key(shortcuts.get(key, key))
    u = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": k,
                "ResponseContentType": "text/html",          # force inline render, not download
                "ResponseContentDisposition": "inline"},
        ExpiresIn=int(min(days, 7) * 86400))
    print(u)
    return u


PORTAL_ITEMS = [
    ("dashboards/dashboard_intl.html", "European & UK linkers dashboard",
     "CMT buckets and per-bond financed breakevens - cumulative, auction-cycle and calendar "
     "seasonality, Brent energy hedge, performance stats."),
    ("dashboards/dashboard.html", "US TIPS dashboard",
     "US fixed-maturity breakevens - cumulative returns, seasonality, gasoline hedge."),
    ("dashboards/eventflow.html", "Event Flow - weekend de-risking",
     "Weekend/headline de-risking study: clock-window returns (Thu->London close, weekend gap), "
     "curve vs parallel, headline clock. Hourly futures, 2024->."),
    ("dashboards/eventstudio.html", "Event Studio - supply events",
     "Cross-asset auction/syndication event analysis: concessions & snap-backs, outright / BE / "
     "vs matched Bund/UST / curve / fly, by market, method and tenor."),
]

# Truly-live services (ECS) — direct internal URLs, not S3 artifacts. Reachable on the
# corporate network/VPN only (internal ALB + private DNS).
LIVE_LINKS = [
    ("DTCC inflation swaps tape — LIVE", "Real-time cleared inflation swap prints "
     "(intraday tape, packages, DV01). ECS service — corporate network required.",
     "https://dtcc-tapes.veritionfund.cloud/", "LIVE"),
    ("DTCC rates options tape — LIVE", "Real-time cleared rates-options prints "
     "(structures, families). ECS service — corporate network required.",
     "https://dtcc-tapes.veritionfund.cloud/options", "LIVE"),
]


def portal(days=7):
    """ONE link for everything: builds a small index page linking every dashboard + research PDF
    (each via its own presigned URL), uploads it, and prints a single presigned link to the page.
    Inner content stays fresh automatically (the daily push overwrites the same S3 keys); the whole
    set of links expires together after `days` (max 7) — regenerate weekly and resend one URL."""
    if not enabled():
        print("  [storage] S3 not configured — skipped"); return None
    cli = _client()
    exp = int(min(days, 7) * 86400)

    def _sign(key, ctype):
        return cli.generate_presigned_url("get_object", ExpiresIn=exp, Params={
            "Bucket": BUCKET, "Key": _key(key),
            "ResponseContentType": ctype, "ResponseContentDisposition": "inline"})

    def _list(prefix):
        for page in cli.get_paginator("list_objects_v2").paginate(Bucket=BUCKET,
                                                                  Prefix=_key(prefix)):
            for o in page.get("Contents", []):
                yield o["Key"][len(PREFIX):].lstrip("/") if PREFIX else o["Key"]

    items = []
    for key, title, desc in PORTAL_ITEMS:
        try:
            cli.head_object(Bucket=BUCKET, Key=_key(key))
            items.append((title, desc, _sign(key, "text/html"), "DASHBOARD"))
        except Exception:
            print(f"  (skipping {key} — not in bucket; run a push first)")
    for title, desc, url, tag in LIVE_LINKS:
        items.append((title, desc, url, tag))
    # Live monitors — whatever Hobbes publish.py pushed under monitors/ (html views + xlsx reports)
    for key in _list("monitors/"):
        name = os.path.basename(key)
        if name.startswith("dtcc"):        # tape snapshots superseded by the LIVE ECS links above
            continue
        title = os.path.splitext(name)[0].replace("_", " ").replace("-", " ")
        if key.endswith(".html"):
            items.append((title, "Live monitor — opens in the browser.",
                          _sign(key, "text/html"), "MONITOR"))
        elif key.endswith(".xlsx"):
            u = cli.generate_presigned_url("get_object", ExpiresIn=exp, Params={
                "Bucket": BUCKET, "Key": _key(key),
                "ResponseContentDisposition": f'attachment; filename="{name}"'})
            items.append((title, "Latest report — downloads as Excel.", u, "MONITOR"))
    for key in _list("research/"):
        if key.endswith(".pdf"):
            name = os.path.basename(key).replace("report_", "").replace(".pdf", "")
            items.append((name.replace("_", " "), "Research note (PDF).",
                          _sign(key, "application/pdf"), "RESEARCH"))
    if not items:
        print("  [storage] nothing to link — push dashboards/PDFs first"); return None

    from datetime import date, timedelta
    until = (date.today() + timedelta(days=min(days, 7))).strftime("%b %d")
    cards = "\n".join(
        f'<a class="card" href="{u}" target="_blank"><div class="tag">{tag}</div>'
        f'<h2>{t}</h2><p>{d}</p></a>' for t, d, u, tag in items)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Linkers &amp; TIPS</title>
<style>
 body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f9f9f7;color:#0b0b0b;
      margin:0;padding:48px 24px}}
 .wrap{{max-width:760px;margin:0 auto}} h1{{font-size:26px;margin:0 0 4px}}
 .sub{{color:#52514e;font-size:14px;margin:0 0 28px}}
 .card{{display:block;background:#fcfcfb;border:1px solid #e1e0d9;border-radius:10px;
       padding:18px 20px;margin:0 0 14px;text-decoration:none;color:inherit}}
 .card:hover{{border-color:#2a78d6}}
 .card h2{{font-size:16px;margin:2px 0 6px}} .card p{{font-size:13px;color:#52514e;margin:0}}
 .tag{{font-size:10px;letter-spacing:.08em;color:#898781;font-weight:600}}
 .foot{{color:#898781;font-size:12px;margin-top:24px}}
</style></head><body><div class="wrap">
<h1>Linkers &amp; TIPS</h1>
<p class="sub">Dashboards update automatically with each daily refresh. Links on this page are
valid until {until}.</p>
{cards}
<div class="foot">Generated {date.today():%b %d, %Y} · private — do not forward outside the desk.</div>
</div></body></html>"""

    tmp = os.path.join(HERE, "_portal_tmp.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        cli.upload_file(tmp, BUCKET, _key("portal/index.html"),
                        ExtraArgs={"ContentType": "text/html", "CacheControl": "no-cache"})
    finally:
        os.remove(tmp)
    link = _sign("portal/index.html", "text/html")
    print(f"  portal: {len(items)} items (valid until {until}) — send THIS one link:\n")
    print(link)
    return link


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "push"
    if cmd == "url":
        url(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "portal":
        portal()
    else:
        {"push": push, "push-raw": push_raw, "pull-raw": pull_raw, "identity": identity}.get(cmd, push)()
