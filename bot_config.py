"""Bot runtime configuration (non-secret tunables)."""

from __future__ import annotations

import config

# Coinbase product ids used for underlying ICT context.
TRADED_PRODUCTS: tuple[str, ...] = ("BTC-USD", "ETH-USD")
DEFAULT_PRODUCT_ID = "BTC-USD"

SERIES_TO_PRODUCT: dict[str, str] = {
    "KXBTC15M": "BTC",
    "KXETH15M": "ETH",
    "KXXRP15M": "XRP",
    "KXSOL15M": "SOL",
    "KXHYPE15M": "HYPE",
    # Top-of-hour threshold series, traded by the eva_wick hourly piggyback
    # books only (eva_wick_hourly.py) — never polled by the 15m cycle.
    "KXBTCD": "BTC",
    "KXETHD": "ETH",
}
PRODUCT_TO_COINBASE: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "XRP": "XRP-USD",
    "SOL": "SOL-USD",
    "HYPE": "HYPE-USD",
}

# Paper-only clones of the eva_wick favourite rule, one per altcoin series.
# Same rule, same knobs, different market — the point is to find out whether
# the favourite-longshot mispricing the rule feeds on is a property of these
# 15m binaries generally or only of the two books we happen to have measured.
ALT_WICK_VARIANTS: dict[str, str] = {
    "eva_wick_xrp": "KXXRP15M",
    "eva_wick_sol": "KXSOL15M",
    "eva_wick_hype": "KXHYPE15M",
}

# Hourly piggyback books (eva_wick_hourly.py): blind fixed-rung baskets on
# the top-of-hour BTC/ETH threshold series, placed whenever the live
# eva_wick rule fires. Enabled via ENABLED_BOTS like everything else, but
# they are not registry strategies — run_strategy_cycle hands them the
# tick's fires after the strategy loop.
HOURLY_WICK_BOTS: tuple[str, ...] = ("eva_wick_1h_ladder", "eva_wick_1h_flat")

# Bots that may never send a real order, whatever the env says. This is a
# code-level guard rather than an env one because KALSHI_LIVE_BOTS is a
# whitelist that an operator edits under time pressure.
PAPER_ONLY_BOTS: frozenset[str] = frozenset(ALT_WICK_VARIANTS) | frozenset(
    HOURLY_WICK_BOTS
)

# When True, Telegram only gets DMs on real paper trades (not skips).
# Default False: operator always sees skip rationales (ICT port requirement).
# Env-driven so the eva_wick product channel stays trade-only.
BROADCAST_ONLY_TRADES = config.BROADCAST_ONLY_TRADES

# Pre-broadcast audit refine loop (spot + Kalshi ICT rationale critic).
MAX_REFINE_PASSES = 3
# Spot path default; Kalshi path passes run_llm_critic=True explicitly.
RUN_LLM_CRITIC_PRE_BROADCAST = False
# Kalshi soak: run LLM chart/rationale critic before paper fill.
KALSHI_RUN_LLM_CRITIC = True

# Fixed-fraction spot sizing (legacy ICT path / validate.py).
TRADE_DEPLOY_PCT = 0.25

# M5 OB fib entry band (bullish: from block low; bearish: from block high).
ENTRY_FIB_LOW = 0.25
ENTRY_FIB_HIGH = 0.50
ENTRY_FIB_TRANCHE_1 = 0.25
ENTRY_FIB_TRANCHE_2 = 0.50
ADD_FIB_LEVEL = 0.718
ENTRY_TRANCHE_DEPLOY_PCT = TRADE_DEPLOY_PCT / 2
ADD_DEPLOY_PCT = TRADE_DEPLOY_PCT
FIB_LEVEL_TOLERANCE_PCT = 0.008

# Paper position size guardrails per product (legacy ICT / validate).
PRODUCT_QTY_CAPS: dict[str, tuple[float, float]] = {
    "ETH-USD": (0.25, 2.0),
    "BTC-USD": (0.005, 0.05),
}
MIN_ETH_QTY = PRODUCT_QTY_CAPS["ETH-USD"][0]
MAX_ETH_QTY = PRODUCT_QTY_CAPS["ETH-USD"][1]

# Personal / house book leftovers (imports may still reference these).
PAPER_CONTRIBUTION_USD = 1000.0
HOUSE_CONTRIBUTION_TELEGRAM_ID = 0
PAPER_ACCOUNT_SIZES: tuple[float, ...] = (500.0, 1000.0, 2500.0)
PAPER_ACCOUNT_DEFAULT_USD = 1000.0
APPROVAL_WINDOW_MIN = 15
MISSED_CONNECTION_R = 0.5
USER_MIN_DEPLOY_USD = 25.0
LAUNCH_NOTICE_SENT_KEY = "personal_books_launch_v1"
MAX_OPEN_TRADES = 20

# Minimum OB zone width as % of mid price.
OB_MIN_WIDTH_PCT = 1.25
OB_MIN_WIDTH_PCT_M5 = 0.15
PRODUCT_OB_MIN_WIDTH_PCT: dict[str, float] = {
    "ETH-USD": OB_MIN_WIDTH_PCT,
    "BTC-USD": 0.60,
}

PAPER_EPOCH_LABEL = "kalshi_15m_ict"

# Kalshi LTF scanner between 15m marks (shadow until execute enabled).
# Env-driven (WATCHDOG_ENABLED); watchdog fills as bot_id=control.
WATCHDOG_ENABLED = config.WATCHDOG_ENABLED
WATCHDOG_INTERVAL_SEC = 60
WATCHDOG_COOLDOWN_SEC = 30 * 60
# Watchdog can paper-fill when triggers + edge gates pass.
WATCHDOG_EXECUTE_ENABLED = True
WATCHDOG_EXECUTE_META_KEY = "watchdog_execute_enabled"
WATCHDOG_ALLOW_SHORTS = True
SCALE_IN_MIN_R = 0.5

# Env-driven (MACRO_CONTEXT_ENABLED); macro classify spends Claude tokens.
MACRO_CONTEXT_ENABLED = config.MACRO_CONTEXT_ENABLED
MACRO_POLL_INTERVAL_SEC = 300
MACRO_MIN_SEVERITY_INJECT = 3
MACRO_PULSE_MIN_SEVERITY = 4
MACRO_WATCHDOG_GATE_MIN_SEVERITY = 4
MACRO_DEFAULT_TTL_HOURS = 24
MACRO_LLM_PROMOTE_THRESHOLD = 40

ZMOVE_ENABLED = False
ZMOVE_INTERVAL_SEC = 300
ZMOVE_THRESHOLD = 2.0
ZMOVE_LOOKBACK_H = 168
ZMOVE_COOLDOWN_SEC = 2 * 60 * 60
ZMOVE_PRODUCT_ID = "ETH-USD"

RELATIVE_STRENGTH_ENABLED = False

# Kalshi mirrors (config is source of truth; these are convenient aliases).
KALSHI_SERIES: tuple[str, ...] = tuple(config.KALSHI_SERIES)
KALSHI_MAX_CONTRACTS = config.KALSHI_MAX_CONTRACTS
KALSHI_MIN_EDGE_CENTS = config.KALSHI_MIN_EDGE_CENTS
KALSHI_CYCLE_OFFSET_SEC = config.KALSHI_CYCLE_OFFSET_SEC
KALSHI_PAPER_ONLY = config.KALSHI_PAPER_ONLY
KALSHI_BANKROLL_USD = config.KALSHI_BANKROLL_USD
KALSHI_DEPLOY_PCT = config.KALSHI_DEPLOY_PCT
KALSHI_MAX_DEPLOY_PCT = config.KALSHI_MAX_DEPLOY_PCT
KALSHI_MAX_NOTIONAL_USD = config.KALSHI_MAX_NOTIONAL_USD
KALSHI_USE_LIVE_BALANCE = config.KALSHI_USE_LIVE_BALANCE
KALSHI_LIVE_TAKE_CENTS = config.KALSHI_LIVE_TAKE_CENTS
KALSHI_EXCHANGE_INDEX = config.KALSHI_EXCHANGE_INDEX
KALSHI_LIVE_TIME_IN_FORCE = config.KALSHI_LIVE_TIME_IN_FORCE
KALSHI_LIVE_BOTS = config.KALSHI_LIVE_BOTS


def bot_is_live(bot_id: str | None) -> bool:
    """True when this bot may send real orders (entries and exits).

    KALSHI_PAPER_ONLY=true blankets everything paper. Otherwise, an empty
    KALSHI_LIVE_BOTS keeps the legacy behavior (all bots live), and a
    non-empty list is a whitelist — e.g. KALSHI_LIVE_BOTS=eva_streak keeps
    eva_wick's control book on paper while the streak bot trades the account.
    """
    if str(bot_id or "") in PAPER_ONLY_BOTS:
        return False
    if config.KALSHI_PAPER_ONLY:
        return False
    if not config.KALSHI_LIVE_BOTS:
        return True
    return str(bot_id or "control") in config.KALSHI_LIVE_BOTS


KALSHI_BOT_MAX_CONTRACTS = config.KALSHI_BOT_MAX_CONTRACTS


def bot_max_contracts(bot_id: str | None) -> int:
    """Contract ceiling for one bot — its own cap, else the global one.

    Lets a sleeve that has earned size run bigger without rescaling the other
    books mid-epoch, which would break their comparability.
    """
    own = KALSHI_BOT_MAX_CONTRACTS.get(str(bot_id or ""))
    cap = int(KALSHI_MAX_CONTRACTS)
    return max(0, min(cap, int(own)) if own is not None else cap)

# Conviction × agree/contra deploy matrix (fraction of book).
CONVICTION_HIGH_SCORE = 0.75
CONVICTION_MED_SCORE = 0.45
CONVICTION_HIGH_AGREE_PCT = 0.08
CONVICTION_HIGH_CONTRA_PCT = 0.12
CONVICTION_MED_AGREE_PCT = 0.03
CONVICTION_MED_CONTRA_PCT = 0.06
CONVICTION_LOW_AGREE_PCT = 0.005
CONVICTION_LOW_CONTRA_PCT = 0.015
ADVERSE_SIZE_BOOST_MAX = 1.25
ADVERSE_BOOST_CHEAP_SPAN_CENTS = 25.0

# Main loop cadence.
KALSHI_JOB_INTERVAL_SEC = 60
# How wide a window around (open + offset) still counts as "decision time".
KALSHI_DECISION_WINDOW_SEC = 90
# Soft mid filter: skip lottery-ticket binaries even with ICT bias.
KALSHI_EXTREME_MID_CENTS = 5.0

# When set, live cycle appends mid/spot snapshots for backtest fidelity.
# Example: "kalshi_snapshots.db" (relative to repo root) or absolute path.
KALSHI_SNAPSHOT_DB: str | None = None

# Multi-bot paper experiments (comma-separated bot_ids via ENABLED_BOTS env).
# Default control = conviction product path (every 15m).
ENABLED_BOTS: tuple[str, ...] = tuple(config.ENABLED_BOTS)
BOT_DISPLAY_NAMES: dict[str, str] = {
    "control": "Control (conviction ICT)",
    "lottery": "Lottery / hail-mary",
    "adverse": "Adverse / wick-hunt",
    "eva_wick": "EVA favourite (mid-window)",
    "eva_wick_fade_v1": "EVA wick fade (retired 2026-09-17)",
    "eva_streak": "EVA reversal",
    "eva_arb": "EVA arb",
    "eva_wick_xrp": "EVA favourite · XRP (paper)",
    "eva_wick_sol": "EVA favourite · SOL (paper)",
    "eva_wick_hype": "EVA favourite · HYPE (paper)",
    "eva_wick_1h_ladder": "EVA 1h ladder · wick piggyback (paper)",
    "eva_wick_1h_flat": "EVA 1h flat · wick piggyback (paper)",
}


def active_series() -> tuple[str, ...]:
    """Series the cycle polls: the core books plus any enabled altcoin clone.

    An altcoin series is only polled when its bot is in ENABLED_BOTS, so the
    default profile makes exactly the same Kalshi calls it made before.
    """
    out = list(KALSHI_SERIES)
    for bot_id in ENABLED_BOTS:
        series = ALT_WICK_VARIANTS.get(bot_id)
        if series and series not in out:
            out.append(series)
    return tuple(out)

# Shared ICT/HTF Claude refresh (aliases to config).
HTF_REFRESH_MODE: str = config.HTF_REFRESH_MODE
HTF_BIAS_TTL_SEC: int = config.HTF_BIAS_TTL_SEC
HTF_M5_MOVE_PCT: float = config.HTF_M5_MOVE_PCT
HTF_REFRESH_ON_H1_CLOSE: bool = config.HTF_REFRESH_ON_H1_CLOSE

# Lottery bot: last N minutes; cancel unfilled limits this many minutes before expiry.
LOTTERY_WINDOW_MINUTES = 5.0
LOTTERY_CANCEL_MINUTES_BEFORE_EXPIRY = 1.5  # ~13:30 of a 15m window
LOTTERY_COINFLIP_MIN_CENTS = 45.0
LOTTERY_COINFLIP_MAX_CENTS = 55.0
LOTTERY_COINFLIP_LIMIT_MIN_CENTS = 7.0
LOTTERY_COINFLIP_LIMIT_MAX_CENTS = 10.0
LOTTERY_MAX_CONTRACTS = 5
LOTTERY_DEPLOY_PCT = 0.05

# Adverse / wick-hunt: enter after move against shared bias.
ADVERSE_MAX_ENTRY_CENTS = 40.0
ADVERSE_MIN_ENTRY_CENTS = 15.0  # skip lottery-cheap tickets (live 0–15¢ bled)
ADVERSE_MIN_EXCURSION_PCT = 0.05  # min |spot vs strike| adverse move to arm fill
ADVERSE_MIN_MID_IMPROVEMENT_CENTS = 5.0  # side mid must cheapen vs arm mid
ADVERSE_MAX_MID_IMPROVEMENT_CENTS = 15.0  # skip blow-off cheapening (trend, not wick)
ADVERSE_MIN_ARM_SIDE_MID_CENTS = 35.0  # do not arm when side already cheap
# Stub for later: allow last-3m block exception on strong HTF + cheap underdog.
STRONG_SIGNAL_OVERRIDE = False

# EVA wick bot — redefined 2026-09-17 as "buy the mid-window favourite".
# The old fade strategy (cheap 20-33¢ side on a wick setup) is retired: an
# out-of-sample search over 2,051 logged cycles found that buying *blind* in
# the 20-33¢ band loses ~11 points against the price paid, and the old sleeve
# lost 10.7 — its trigger, M15 gate and excursion filters added nothing at all.
# The favourite side is mispriced the other way, but only mid-window:
#   12.5-15 min left  +2.7 pts (t=0.3)   <- where the old bot traded
#   4-10 min left    +13.0 pts (t=3.5)   <- this rule
#   last 2 min        -4.9 pts (t=-3.3)  <- where eva_arb trades and loses
# Epoch backtest of exactly this rule: 151 entries, 86.1% win at 73.3¢,
# +$0.098/contract at mid+1¢ (t=3.48), 13/15 days green, worst drawdown
# $1.84/contract, and still profitable paying 6¢ through the mid — which is why
# it can ship before we have recorded bid/ask for this part of the window.
EVA_FAV_MIN_ENTRY_CENTS = 67.0
EVA_FAV_MAX_ENTRY_CENTS = 80.0
# Qualify on the mid, pay the ask. A mid-priced limit only fills when the book
# comes to us, which selects against a favourite sleeve — the price runs away
# exactly when the tape confirms the side (first live attempt, 2026-09-17
# 16:36, missed a winner this way). Crossing is affordable because the rule
# stays positive at mid+6¢; paying past this cap is not, and the skips record
# the wide-book cases we have no historical bid/ask for.
EVA_FAV_MAX_PAY_CENTS = 82.0
# The clock is half the rule; outside this the same trade is fair or negative.
EVA_FAV_MIN_SECONDS_LEFT = 240.0
EVA_FAV_MAX_SECONDS_LEFT = 600.0
# Altcoin clones only (eva_wick_xrp/sol/hype). The rule is unchanged — the
# max-pay cap above is already the spread guard, since a wide book pushes the
# ask past 82¢ and the entry is skipped. What these books additionally need is
# fill realism. The live book learns its fill from the exchange (fill_count
# and average_fill_price go straight into the ledger, partials included); a
# paper clone has to reconstruct it, by walking the published ask ladder with
# the same limit a live IOC would carry. Turning this off fills the whole clip
# at the touch, which is what every other paper book in this ledger does.
#
# OFF since 2026-09-30, first hour of the epoch: the ladder walk booked a SOL
# entry at 61.0c against a quoted 69.0c ask — a fill 8c through the touch on
# the favourable side, which the exchange cannot give. The /orderbook arrays
# do not mean what ask_ladder_from_orderbook assumes: on a live sample the
# quoted touch could not be reconstructed from either array under either
# orientation (deploy/_ladder_orientation.py), and the level sizes do not
# match yes_bid_size_fp / yes_ask_size_fp, so the depth numbers are not
# trustworthy either. Until that is pinned down, these books fill at the ask
# like the live book does, which is also the strict-comparability answer the
# books exist for. The walk and its tests stay in place behind this flag.
EVA_FAV_ALT_TRIM_TO_DEPTH = False

# EVA streak bot (streak-reversal signal, mid entry since 2026-09-08).
# Evidence: backtest/dan_rules_study.py + dan_rules_pricing.py — reversal after
# k>=3 same-direction 15m candles hits 52-56% while the market charges ~51c.
# backtest/eva_streak_bt.py: the 35c resting-limit entry was adverse selection
# (only fills when the streak keeps running) — mid entry keeps every signal.
# Live epoch (2026-09-08 flip): entries <45c went 0/7 (−$1.83) — cheap mid on
# the reversal side means the streak is still priced to continue. Entries >65c
# are already-won tickets. Band 45–65 was the only profitable slice.
EVA_STREAK_MIN_RUN = 3  # consecutive same-direction 15m candles required
EVA_STREAK_MAX_LOOKBACK = 8  # candles inspected for the run (fetch bound)
EVA_STREAK_REQUIRE_SWEEP = True  # last run candle must take the prior extreme
EVA_STREAK_MIN_SIDE_MID = 45.0  # below this the market still prices continuation
EVA_STREAK_MAX_SIDE_MID = 65.0  # above this the reversal is already priced in
# Hot-tape gate — DISABLED 2026-09-14 after failing its out-of-sample test.
# It shipped on 2026-09-10 in-sample evidence (entries with the trailing hour
# still moving >0.4% were 5W/14L). Scoring the 31 setups it actually blocked
# against recorded settlements gave +$0.060/trade vs +$0.058/trade for the
# setups it allowed — no discrimination at all, while suppressing ~35% of the
# sample we need to decide whether this strategy has an edge. The mechanism is
# kept (config-driven) so it can be re-tested; 0 disables.
EVA_STREAK_MAX_ABS_1H_RET_PCT = 0.0
EVA_STREAK_TP_MULTIPLE = 2.0  # cash the reversal when the side doubles
EVA_STREAK_SL_FRACTION = 0.5  # cut when the side halves — "never fully lose"
EVA_STREAK_COOLDOWN_LOSSES = 2  # this many consecutive SL cuts ...
EVA_STREAK_COOLDOWN_MINUTES = 90.0  # ... pauses new entries this long

# EVA arb bot (last-2-min inefficiency, paper since 2026-09-08). Premise:
# Kalshi stops taking prices ~45s before close, so a favorite that already
# touched 90c and dips back to 75-85c late is a discounted near-certain
# winner IF the dip is noise. Logger evidence so far is mixed (~70% settle
# vs ~80c cost) — paper-only until the book proves otherwise.
EVA_ARB_WINDOW_MINUTES = 2.0  # only act inside the final N minutes
EVA_ARB_TOUCH_CENTS = 90.0  # favored side must have printed >= this once
EVA_ARB_MIN_ENTRY_CENTS = 75.0  # buy zone lower bound (side mid)
EVA_ARB_MAX_ENTRY_CENTS = 85.0  # buy zone upper bound (side mid)
EVA_ARB_MIN_SECONDS_LEFT = 50.0  # Kalshi stops matching ~45s out — too late


def qty_caps(product_id: str) -> tuple[float, float]:
    """Return (min_qty, max_qty) for a product; fall back to ETH caps."""
    return PRODUCT_QTY_CAPS.get(product_id, PRODUCT_QTY_CAPS["ETH-USD"])


def ob_min_width_pct(product_id: str | None = None) -> float:
    """HTF OB/breaker minimum width (% of mid) for a product."""
    if not product_id:
        return OB_MIN_WIDTH_PCT
    return float(PRODUCT_OB_MIN_WIDTH_PCT.get(product_id, OB_MIN_WIDTH_PCT))


def product_label(product_id: str) -> str:
    pid = str(product_id or "").upper()
    if pid in ("BTC", "BTC-USD") or (pid.endswith("-USD") and pid.startswith("BTC")):
        return "BTC"
    if pid in ("ETH", "ETH-USD") or (pid.endswith("-USD") and pid.startswith("ETH")):
        return "ETH"
    if pid.endswith("-USD"):
        return pid[: -len("-USD")]
    return pid or "UNKNOWN"


def series_product(series: str) -> str:
    return SERIES_TO_PRODUCT.get(
        series.upper(),
        series.upper().replace("KX", "").replace("15M", "")[:3] or "BTC",
    )


def watchdog_execute_enabled() -> bool:
    """Effective watchdog paper-execution flag (config default + runtime meta override)."""
    try:
        import user_books

        raw = user_books.get_meta(WATCHDOG_EXECUTE_META_KEY)
    except Exception:
        raw = None
    if raw is None or str(raw).strip() == "":
        return bool(WATCHDOG_EXECUTE_ENABLED)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def set_watchdog_execute_enabled(enabled: bool) -> bool:
    """Persist runtime override for watchdog paper execution. Returns new value."""
    try:
        import user_books

        user_books.set_meta(WATCHDOG_EXECUTE_META_KEY, "1" if enabled else "0")
    except Exception:
        pass
    return enabled
