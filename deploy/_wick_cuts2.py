"""Second-pass cuts: M15 agreement, streak clustering, TP math."""
import json
import os
import re
from collections import defaultdict

p = os.path.join(os.environ["TEMP"], "kalshi_dump.json")
raw = open(p, "rb").read()
enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
d = json.loads(raw.decode(enc))

positions = [r for r in d["positions"] if r["bot_id"] == "eva_wick"]
decisions = {r["position_id"]: r for r in d["decisions_opened"] if r.get("position_id")}

rows = []
for pos in positions:
    dec = decisions.get(pos["id"], {})
    tags = dec.get("setup_tags") or ""
    # Parse M15 stance/conf from the broadcast rationale: "M15 bullish 0.62"
    rat = pos.get("rationale") or ""
    m = re.search(r"M15 (\w+) (\d+\.\d{2})", rat)
    m15_stance, m15_conf = (m.group(1), float(m.group(2))) if m else (None, None)
    rows.append({
        "id": pos["id"], "opened_at": pos["opened_at"], "product": pos["product_id"],
        "side": pos["side"], "entry": float(pos["entry_cents"]),
        "cost": float(pos["entry_cents"]) / 100 * int(pos["contracts"]),
        "pnl": pos["pnl_usd"], "tags": tags, "pattern": dec.get("trigger_name"),
        "m15_stance": m15_stance, "m15_conf": m15_conf,
    })

settled = [r for r in rows if r["pnl"] is not None]

def cohort(key_fn, label, data=None):
    groups = defaultdict(list)
    for r in (data or settled):
        groups[key_fn(r)].append(r)
    print(f"\n-- {label} --")
    for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        w = sum(1 for r in g if r["pnl"] > 0)
        net = sum(r["pnl"] for r in g)
        cost = sum(r["cost"] for r in g)
        print(f"  {str(k):<36} n={len(g):>3} win={w/len(g):>4.0%} net=${net:+8.2f} roi={net/cost*100 if cost else 0:+6.1f}%")

def m15_agree(r):
    if not r["m15_stance"]:
        return "unknown"
    agrees = (r["side"] == "YES" and r["m15_stance"] == "bullish") or (
        r["side"] == "NO" and r["m15_stance"] == "bearish")
    disagrees = (r["side"] == "YES" and r["m15_stance"] == "bearish") or (
        r["side"] == "NO" and r["m15_stance"] == "bullish")
    lab = "agrees" if agrees else ("disagrees" if disagrees else "neutral")
    return lab

cohort(m15_agree, "M15 stance vs bought side")
cohort(lambda r: (m15_agree(r), "conf>=0.65" if (r["m15_conf"] or 0) >= 0.65 else "conf<0.65"),
       "M15 agreement x confidence")
cohort(lambda r: (r["pattern"], m15_agree(r)), "pattern x M15 agreement")

# Streaks: consecutive losses within same product+side.
print("\n-- re-entry after loss (same product+side within 45 min) --")
by_ps = defaultdict(list)
for r in sorted(settled, key=lambda r: r["opened_at"]):
    by_ps[(r["product"], r["side"])].append(r)
from datetime import datetime
reentry, fresh = [], []
for key, g in by_ps.items():
    for prev, cur in zip(g, g[1:]):
        t0 = datetime.fromisoformat(prev["opened_at"])
        t1 = datetime.fromisoformat(cur["opened_at"])
        if (t1 - t0).total_seconds() <= 45 * 60 and prev["pnl"] <= 0:
            reentry.append(cur)
        else:
            fresh.append(cur)
w = sum(1 for r in reentry if r["pnl"] > 0)
net = sum(r["pnl"] for r in reentry)
cost = sum(r["cost"] for r in reentry)
print(f"  re-entries after a loss:  n={len(reentry)} win={w/max(1,len(reentry)):.0%} net=${net:+.2f} roi={net/cost*100 if cost else 0:+.1f}%")
w2 = sum(1 for r in fresh if r["pnl"] > 0)
net2 = sum(r["pnl"] for r in fresh)
cost2 = sum(r["cost"] for r in fresh)
print(f"  other follow-on entries:  n={len(fresh)} win={w2/max(1,len(fresh)):.0%} net=${net2:+.2f} roi={net2/cost2*100 if cost2 else 0:+.1f}%")

# TP math.
tp_needed = 0.5
print(f"\n-- TP-at-2x math --")
n = len(settled)
tp = sum(1 for r in settled if r["pnl"] > 0 and r["pnl"] <= r["cost"] * 1.5)
big = [r for r in settled if r["pnl"] > r["cost"] * 1.5]
print(f"  closed={n}, wins capped near +100% (TP): {tp}, settle-size wins: {len(big)}")
print(f"  TP rate = {sum(1 for r in settled if r['pnl'] > 0)/n:.0%}; breakeven for a 2x-TP-only book is 50% pre-fee")
