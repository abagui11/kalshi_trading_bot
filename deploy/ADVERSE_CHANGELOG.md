# Adverse-sensitive changelog

Log every deploy that can change adverse direction, fills, or the shared HTF bias
adverse arms from. Tag **ADVERSE_SENSITIVE: yes** when that applies.

---

## 2026-07-28 — HTF refresh cadence + adverse-only default

**ADVERSE_SENSITIVE:** yes  
**Risk:** low (adverse arm/fill rules unchanged; only bias refresh frequency + enabled bots)

### What changed
- Default `ENABLED_BOTS=adverse` — control and lottery off so they cannot keep Claude HTF warm.
- `HTF_REFRESH_MODE=once_per_window` (default) — at most one Claude ICT bias call per 15m ticker per product (was ~2–3× inside the near-decision band).
- Product-level HTF store + `ttl_event` mode available via env (60m TTL + 0.20% M5 move + H1 close) without another code change.
- Full H4/H1/M5 pack + refine/LLM critic still run on every actual refresh.

### What did NOT change
- `strategies/adverse.py` arm / excursion / max entry / coinflip band
- `ADVERSE_*` thresholds
- 60s wick polling cadence

### Knobs
```
ENABLED_BOTS=adverse
HTF_REFRESH_MODE=once_per_window
HTF_BIAS_TTL_SEC=3600
HTF_M5_MOVE_PCT=0.20
HTF_REFRESH_ON_H1_CLOSE=true
```

### Rollback
```
HTF_REFRESH_MODE=every_near_tick
# and/or restore multi-bot soak:
ENABLED_BOTS=control,lottery,adverse
```

### Soak checks
- Adverse daily PnL, WR, avg entry ¢, fills/day
- Claude $ on this API key
- No new control/lottery fills unless re-enabled
