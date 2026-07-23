"""One-shot: print structure + force vision cycle."""
from __future__ import annotations

import json
from patterns.market_structure_state import load_structure_state
from kalshi_cycle import run_once

for p in ("BTC-USD", "ETH-USD"):
    s = load_structure_state(p)
    print(
        "BEFORE",
        p,
        "bias=",
        s.htf_bias,
        "phase=",
        s.setup_phase,
        "updated=",
        s.updated_at,
    )

print("Running force_decision cycle...")
out = run_once(force_decision=True)
print("near_decision=", out.get("near_decision"))
print("n_decisions=", len(out.get("decisions") or []))
for d in out.get("decisions") or []:
    print(
        "DEC",
        d.get("bot_id"),
        d.get("product_id"),
        d.get("side"),
        (d.get("rationale") or "")[:100],
    )

for p in ("BTC-USD", "ETH-USD"):
    s = load_structure_state(p)
    print(
        "AFTER",
        p,
        "bias=",
        s.htf_bias,
        "phase=",
        s.setup_phase,
        "updated=",
        s.updated_at,
    )
    print(" thesis=", (s.window_thesis or "")[:120])
