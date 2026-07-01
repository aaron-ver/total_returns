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
Usage:       python storage.py           # push consumable outputs to S3 (if configured)
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


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    push()
