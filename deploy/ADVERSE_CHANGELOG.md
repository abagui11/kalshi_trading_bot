# Adverse-sensitive changelog

Log every deploy that can change adverse direction, fills, or the shared HTF bias
adverse arms from. Tag **ADVERSE_SENSITIVE: yes** when that applies.

---

## 2026-07-30 — Restore paper-era HTF refresh cadence

**ADVERSE_SENSITIVE:** yes  
**Risk:** low–med (adverse arm rules unchanged; Claude HTF refreshes ~2–3× more often → higher API $)

### What changed
- `HTF_REFRESH_MODE=every_near_tick` again (paper-soak cadence).
- Repo default / `.env.example` match paper era (was `once_per_window` cost cut).
- Still `ENABLED_BOTS=adverse` — no control/lottery re-enable.

### What did NOT change
- `strategies/adverse.py` arm / excursion / max entry / coinflip band
- Live notional caps (`KALSHI_MAX_NOTIONAL_USD`)

### Rollback
```
HTF_REFRESH_MODE=once_per_window
systemctl restart kalshi-agent.service
```

### Soak checks
- `htf_refresh_reason=forced` on near ticks (not only `window_first` / `reuse`)
- Claude $ vs prior ~$30/day once_per_window baseline
- Adverse WR / mix after ~40 fills

---

## 2026-07-28 — Live sizing hard caps + dashboard live/archive

**ADVERSE_SENSITIVE:** yes  
**Risk:** med (live money path tightened; watchdog gated)

### What changed
- `kalshi_sizing.py`: size off `min(live_balance, KALSHI_BANKROLL_USD)`; hard `KALSHI_MAX_NOTIONAL_USD` (default $5) + `MAX_CONTRACTS`.
- `place_order` / `apply_and_log` refuse oversized orders; no shadow open if order fails.
- Watchdog skips entirely unless `control` is in `ENABLED_BOTS` (was still able to live-fill as control).
- Dashboard: LIVE/PAPER badge, primary=enabled bot (adverse), risk caps in subtitle, archived paper CSV list.

### Rollback
Revert commit / set `KALSHI_PAPER_ONLY=true`.

---


**ADVERSE_SENSITIVE:** yes  
**Risk:** high (real money)

### What changed
- `KALSHI_PAPER_ONLY=false` — `place_order` hits Kalshi wallet.
- Shadow paper book reset to wallet start (~$76.47); prior paper epoch archived under `archives/paper_*` + CSV export.
- Still `ENABLED_BOTS=adverse`, `HTF_REFRESH_MODE=once_per_window`, `KALSHI_MAX_CONTRACTS=5`.
- `KALSHI_USE_LIVE_BALANCE=true` for sizing vs wallet.

### What did NOT change
- Adverse arm/fill thresholds
- HTF once-per-window cadence

### Rollback
```
KALSHI_PAPER_ONLY=true
systemctl restart kalshi-agent.service
```

### Soak checks
- First live fill appears in Kalshi UI and shadow ledger
- Settlement matches Kalshi result
- Claude $ and adverse PnL vs wallet

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
