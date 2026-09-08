"""Trade-by-trade autopsy of eva_wick positions from the VPS ledger dump."""
import json
import os
import re
import sys
from collections import defaultdict

p = os.path.join(os.environ["TEMP"], "kalshi_dump.json")
raw = open(p, "rb").read()
enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
d = json.loads(raw.decode(enc))

positions = [r for r in d["positions"] if r["bot_id"] == "eva_wick"]
decisions = {r["position_id"]: r for r in d["decisions_opened"] if r.get("position_id")}
trades = d["trades"]

tp_events = defaultdict(list)
for t in trades:
    if t["bot_id"] == "eva_wick" and t["event"] not in ("open", "settle"):
        tp_events[t["market_ticker"]].append(t)

def parse_trigger(reason):
    out = {}
    if not reason:
        return out
    m = re.match(r"(\w+):", reason)
    if m:
        out["pattern"] = m.group(1)
    for key in ("session_pos", "excursion", "btc_1h", "day_range"):
        m = re.search(rf"{key}=([+-]?[\d.]+|None)", reason)
        if m and m.group(1) != "None":
            out[key] = float(m.group(1))
    m = re.search(r"quarter=(\w+)", reason)
    if m:
        out["quarter"] = m.group(1)
    return out

rows = []
for pos in positions:
    dec = decisions.get(pos["id"], {})
    trig = {}
    for field in ("gate_outcome", "would_skip_reasons", "rationale"):
        t2 = parse_trigger(dec.get(field) or "")
        if "session_pos" in t2 or ("pattern" in t2 and not trig):
            trig = {**trig, **t2}
        if "session_pos" in trig:
            break
    entry = float(pos["entry_cents"])
    contracts = int(pos["contracts"])
    pnl = pos["pnl_usd"]
    tps = tp_events.get(pos["market_ticker"], [])
    is_tp = bool(tps)
    tags = dec.get("setup_tags") or ""
    rows.append({
        "id": pos["id"],
        "opened_at": pos["opened_at"],
        "ticker": pos["market_ticker"],
        "product": pos["product_id"],
        "side": pos["side"],
        "ct": contracts,
        "entry": entry,
        "cost": round(entry / 100 * contracts, 2),
        "result": pos["result"],
        "pnl": pnl,
        "exit": "TP" if is_tp else "settle",
        "pattern": dec.get("trigger_name") or trig.get("pattern"),
        "tags": tags,
        "deploy_pct": dec.get("deploy_pct"),
        "quarter": trig.get("quarter"),
        "session_pos": trig.get("session_pos"),
        "excursion": trig.get("excursion"),
        "btc_1h": trig.get("btc_1h"),
        "ict_bias": dec.get("ict_bias"),
        "sec_to_exp": dec.get("seconds_to_expiry"),
        "rationale": (pos.get("rationale") or ""),
    })

what = sys.argv[1] if len(sys.argv) > 1 else "summary"

def cohort(settled, key_fn, label):
    groups = defaultdict(list)
    for r in settled:
        groups[key_fn(r)].append(r)
    print(f"\n-- by {label} --")
    for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        w = sum(1 for r in g if r["pnl"] > 0)
        tp = sum(1 for r in g if r["exit"] == "TP")
        net = sum(r["pnl"] for r in g)
        cost = sum(r["cost"] for r in g)
        roi = net / cost * 100 if cost else 0
        print(f"  {str(k):<32} n={len(g):>3} win={w/len(g):>4.0%} tp_rate={tp/len(g):>4.0%} net=${net:+8.2f} roi={roi:+6.1f}%")

if what == "summary":
    settled = [r for r in rows if r["pnl"] is not None]
    wins = [r for r in settled if r["pnl"] > 0]
    tot_cost = sum(r["cost"] for r in settled)
    print(f"eva_wick: {len(settled)} closed | {len(wins)} wins ({len(wins)/len(settled):.0%}) | "
          f"net ${sum(r['pnl'] for r in settled):+.2f} on ${tot_cost:.2f} risked "
          f"({sum(r['pnl'] for r in settled)/tot_cost:+.1%} ROI)")
    tp = [r for r in settled if r["exit"] == "TP"]
    print(f"TP exits: {len(tp)} ({len(tp)/len(settled):.0%}) | settle wins: "
          f"{sum(1 for r in settled if r['exit']=='settle' and r['pnl']>0)} | "
          f"settle losses: {sum(1 for r in settled if r['exit']=='settle' and r['pnl']<=0)}")
    cohort(settled, lambda r: r["pattern"], "pattern")
    cohort(settled, lambda r: r["product"], "product")
    cohort(settled, lambda r: (r["product"], r["side"]), "product+side")
    cohort(settled, lambda r: r["side"], "side")
    cohort(settled, lambda r: r["quarter"], "quarter")
    cohort(settled, lambda r: r["opened_at"][:10], "day")
    cohort(settled, lambda r: ("<=15" if r["entry"] <= 15 else "16-25" if r["entry"] <= 25 else "26-33" if r["entry"] <= 33 else ">33"), "entry price band")
    cohort(settled, lambda r: "counter_htf" if "counter_htf" in (r["tags"] or "") else ("aligned_htf" if "aligned_htf" in (r["tags"] or "") else "unknown"), "HTF alignment")
    def softs(r):
        tags = r["tags"] or ""
        s = sorted(t.strip(' "') for t in re.findall(r'"(soft_\w+)"', tags))
        return ",".join(s) or ("(none)" if tags else "unknown")
    cohort(settled, softs, "soft gates")
    def sess_bucket(r):
        sp = r["session_pos"]
        if sp is None:
            return "unknown"
        if sp <= 0.05: return "0-0.05 (extreme low)"
        if sp <= 0.30: return "0.05-0.30 (low)"
        if sp >= 0.95: return "0.95-1.0 (extreme high)"
        if sp >= 0.70: return "0.70-0.95 (high)"
        return "mid ?!"
    cohort(settled, sess_bucket, "session position")
    def btc_bucket(r):
        b = r["btc_1h"]
        if b is None: return "unknown"
        a = abs(b)
        if a <= 0.15: return "<=0.15%"
        if a <= 0.30: return "0.15-0.30%"
        if a <= 0.50: return "0.30-0.50%"
        return ">0.50% (soft zone)"
    cohort(settled, btc_bucket, "abs BTC 1h move")
    def exc_bucket(r):
        e = r["excursion"]
        if e is None: return "unknown"
        a = abs(e)
        if a <= 0.05: return "0.03-0.05%"
        if a <= 0.10: return "0.05-0.10%"
        if a <= 0.20: return "0.10-0.20%"
        return ">0.20%"
    cohort(settled, exc_bucket, "abs excursion through strike")
    # hour of day (UTC)
    cohort(settled, lambda r: r["opened_at"][11:13] + "h", "hour (UTC)")
elif what == "skips":
    for r in d["skip_counts"]:
        if r["bot_id"] == "eva_wick":
            print(f'{r["n"]:>5}  {r["skip_codes"]}')
elif what == "table":
    for r in rows:
        print(f'{r["id"]:>4} {r["opened_at"][:16]} {r["product"]:<4} {r["side"]:<3} '
              f'@{r["entry"]:>4.0f}c ${r["cost"]:>6.2f} {str(r["result"]):<4} '
              f'pnl={r["pnl"]:>7.2f} {r["exit"]:<6} {str(r["pattern"]):<14} '
              f'q={str(r["quarter"]):<7} sp={r["session_pos"]} exc={r["excursion"]} btc={r["btc_1h"]}')
elif what == "json":
    print(json.dumps(rows, indent=1, default=str))
elif what == "state":
    print(json.dumps(d["paper_state"], indent=1))
