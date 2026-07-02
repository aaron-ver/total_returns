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
Usage:       python storage.py            # push consumable ARTIFACTS to S3 (marts/exports/plots/dashboards)
             python storage.py push-raw   # TERMINAL box: upload raw caches (cache/, cache_intl/) after the pull
             python storage.py pull-raw   # CLOUD/any box: download raw caches so BUILD can run without a terminal
"""
from __future__ import annotations
import os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = os.environ.get("LINKERS_S3_BUCKET")
PREFIX = os.environ.get("LINKERS_S3_PREFIX", "").strip("/")

# local dir -> S3 sub-prefix. Consumable outputs only (raw caches are regenerable — not synced).
DIRS = {"marts": "marts", "exports": "exports", "plots": "plots"}
FILES = {"dashboard_intl.html": "dashboards/dashboard_intl.html",
         "dashboard.html": "dashboards/dashboard.html"}

# The RAW pulled caches. Pushed by the TERMINAL box right after the Bloomberg pull; pulled by a
# headless/cloud box (or any teammate) before BUILD — so the compile can run anywhere, not just
# where Bloomberg is. This is what makes the data live in S3 instead of one machine.
RAW_DIRS = {"cache": "raw/cache", "cache_intl": "raw/cache_intl"}


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


def push():
    if not enabled():
        print("  [storage] S3 not configured (set LINKERS_S3_BUCKET + AWS creds, pip install boto3) — skipped")
        return 0
    cli = _client(); total = 0
    print(f"  [storage] sync -> s3://{BUCKET}/{PREFIX or ''}")
    for d, sub in DIRS.items():
        base = os.path.join(HERE, d)
        if not os.path.isdir(base):
            continue
        n = 0
        for path in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if os.path.isfile(path):
                rel = os.path.relpath(path, base).replace("\\", "/")
                cli.upload_file(path, BUCKET, _key(sub, rel)); n += 1
        total += n; print(f"    {d:10s} -> {sub}/  ({n} files)")
    for f, key in FILES.items():
        p = os.path.join(HERE, f)
        if os.path.isfile(p):
            cli.upload_file(p, BUCKET, _key(key)); total += 1; print(f"    {f} -> {key}")
    print(f"  [storage] uploaded {total} files")
    return total


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
    cli = _client(); total = 0
    print(f"  [storage] push RAW -> s3://{BUCKET}/{_key('raw') or 'raw'}")
    for d, sub in RAW_DIRS.items():
        base = os.path.join(HERE, d)
        if not os.path.isdir(base):
            continue
        n = 0
        for path in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if os.path.isfile(path):
                rel = os.path.relpath(path, base).replace("\\", "/")
                cli.upload_file(path, BUCKET, _key(sub, rel)); n += 1
        total += n; print(f"    {d:10s} -> {sub}/  ({n} files)")
    print(f"  [storage] uploaded {total} raw files")
    return total


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


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "push"
    {"push": push, "push-raw": push_raw, "pull-raw": pull_raw, "identity": identity}.get(cmd, push)()
