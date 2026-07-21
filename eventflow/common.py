"""
eventflow — shared plumbing for the weekend/headline event-flow study (boss spec 2026-07).

HTTP: the corporate proxy re-signs TLS with a cert chain that fails strict validation
(observed: "Missing Authority Key Identifier" on some domains). Data still flows; the proxy IS
the network trust boundary here, so we fetch with verification relaxed — centralized in http_get
with this note, used for Yahoo/GDELT only (public market/news data, nothing sensitive sent).
"""
from __future__ import annotations
import json
import os
import ssl
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "out")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE          # corporate MITM proxy; see module docstring
_HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()


def http_json(url, timeout=60):
    return json.loads(http_get(url, timeout))


# ---- clock conventions (all analysis in US/Eastern; futures trade nearly 23h) ----
TZ = "America/New_York"
LONDON_CLOSE_ET = 11.5      # ~16:30 London ≈ 11:30 ET (DST-aligned most of the year)
US_RATES_CLOSE_ET = 15.0    # 3pm ET treasury/futures settlement
US_EQUITY_CLOSE_ET = 16.0
ASIA_REOPEN_ET = 18.0       # Sunday 6pm ET = futures week open


def to_et(ts_utc_index):
    return ts_utc_index.tz_localize("UTC").tz_convert(TZ) if ts_utc_index.tz is None \
        else ts_utc_index.tz_convert(TZ)
