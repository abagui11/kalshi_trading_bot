"""Patch .env for conviction product bot (control every 15m, 15% book cap, macro on).

Run from repo root (local or server):
  python deploy/patch_conviction_env.py
"""

from __future__ import annotations

from pathlib import Path

p = Path(".env")
if not p.is_file():
    raise SystemExit(f"missing {p.resolve()}")

text = p.read_text(encoding="utf-8")
reps = {
    "ENABLED_BOTS": "control",
    "KALSHI_MAX_CONTRACTS": "100",
    "KALSHI_MAX_DEPLOY_PCT": "0.15",
    "KALSHI_MAX_NOTIONAL_USD": "0",
    # Keep bankroll / paper mode; only raise deploy ceiling via MAX_DEPLOY_PCT.
    "KALSHI_DEPLOY_PCT": "0.05",
}
# Ensure MACRO_FEED_URLS exists with sane defaults if absent.
macro_default = (
    "https://www.federalreserve.gov/feeds/press_all.xml,"
    "https://www.cnbc.com/id/10000664/device/rss/rss.html,"
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
)

lines = text.splitlines()
out: list[str] = []
seen: set[str] = set()
for line in lines:
    if (not line.strip()) or line.strip().startswith("#") or ("=" not in line):
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in reps:
        out.append(f"{k}={reps[k]}")
        seen.add(k)
    else:
        out.append(line)
        seen.add(k)

for k, v in reps.items():
    if k not in seen:
        out.append(f"{k}={v}")

if "MACRO_FEED_URLS" not in seen:
    out.append(f"MACRO_FEED_URLS={macro_default}")

p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("conviction env patched:", p.resolve())
for k, v in reps.items():
    print(f"  {k}={v}")
if "MACRO_FEED_URLS" not in seen:
    print("  MACRO_FEED_URLS=<default feeds>")
