"""Print Coinbase M5 window opens/closes near remaining ambiguous trade windows."""
import json
from datetime import datetime, timezone
from pathlib import Path

data = json.loads((Path(__file__).parent / "candles.json").read_text())
m5 = {int(c["start"]): c for c in data["BTC-USD"]["M5"]}


def window(ts_utc: str) -> None:
    ts = int(datetime.fromisoformat(ts_utc).replace(tzinfo=timezone.utc).timestamp())
    bars = [m5[k] for k in sorted(m5) if ts <= k < ts + 900]
    if not bars:
        print(ts_utc, "no data")
        return
    o = float(bars[0]["open"])
    c = float(bars[-1]["close"])
    hi = max(float(b["high"]) for b in bars)
    lo = min(float(b["low"]) for b in bars)
    print(f"{ts_utc}  open={o:.2f} close={c:.2f} hi={hi:.2f} lo={lo:.2f}")


print("T2 candidates (Down strike 77293.99, settle 3:00PM ET => 19:00Z):")
window("2026-09-02T18:45:00")

print("\nT3 candidates (Up strike 77179.48, settle 3:45PM ET => 19:45Z):")
window("2026-09-02T19:30:00")

print("\nT5 (Up strike 77344.21, settle 5:00PM ET => 21:00Z):")
window("2026-09-02T20:45:00")

print("\nT7 candidates (Down strike 77367.46, evening):")
for t in ("2026-09-03T01:15:00", "2026-09-03T01:30:00", "2026-09-03T01:45:00"):
    window(t)

print("\nT8 confirm (Up strike 77327.80, 10:15pm ET settle => 02:00-02:15Z):")
window("2026-09-03T02:00:00")
