"""Position-aware macro pulse — alert + optional early flatten on Kalshi books."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic
import bot_config
import config
import notify
import paper
from macro import store

logger = logging.getLogger(__name__)

PULSE_SYSTEM = """You are a risk advisor for a Kalshi 15-minute crypto binary bot — not a trader.
Given a macro headline and any open YES/NO paper positions, recommend posture only.

Chart structure is primary; macro is supplementary. Do not recommend new entries.

Return JSON only:
- recommendation: one of hold, consider_close, flatten
- rationale: 2-4 sentences for the operator
- per_position: array of {position_id, side, action, note} when positions are open
  (action: hold | consider_close | flatten)

Be measured — avoid panic. Macro alone should rarely demand immediate flat.
Use flatten only when the headline clearly contradicts the open side."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def bias_contradicts_side(eth_bias: str | None, side: str) -> bool:
    """True when macro bias clearly fights the open YES/NO position."""
    bias = str(eth_bias or "neutral").lower()
    side_u = str(side or "").upper()
    if bias == "bearish" and side_u == "YES":
        return True
    if bias == "bullish" and side_u == "NO":
        return True
    return False


def _side_exit_cents(pos: dict[str, Any]) -> float | None:
    """Best-effort side mark from live mid; fall back to entry."""
    ticker = str(pos.get("market_ticker") or "")
    side = str(pos.get("side") or "").upper()
    entry = float(pos.get("entry_cents") or 0)
    try:
        import kalshi_client

        mid = None
        try:
            mid = kalshi_client.get_orderbook_mid(ticker)
        except Exception:
            mid = None
        if mid is None:
            market = kalshi_client.get_market(ticker)
            mid = kalshi_client.mid_cents_from_market(market)
        if mid is None:
            return entry if entry > 0 else None
        if side == "YES":
            return float(mid)
        if side == "NO":
            return 100.0 - float(mid)
    except Exception:
        logger.exception("Failed to fetch mid for flatten %s", ticker)
    return entry if entry > 0 else 50.0


def _try_live_close(pos: dict[str, Any], exit_cents: float) -> dict[str, Any] | None:
    """Best-effort live close by buying the opposite side (locks settlement)."""
    if config.KALSHI_PAPER_ONLY:
        return None
    try:
        import kalshi_client

        side = str(pos["side"]).upper()
        opposite = "NO" if side == "YES" else "YES"
        # Opposite side price ≈ 100 − exit of held side.
        opp_cents = max(1.0, min(99.0, 100.0 - float(exit_cents)))
        return kalshi_client.place_order(
            str(pos["market_ticker"]),
            opposite,
            int(pos["contracts"]),
            yes_price_cents=int(round(opp_cents)),
        )
    except Exception:
        logger.exception("Live macro flatten order failed for pos %s", pos.get("id"))
        return None


def maybe_flatten_contradicted(
    event: dict[str, Any],
    open_positions: list[dict[str, Any]],
    advisory: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flatten open positions contradicted by high-sev bias (or LLM flatten)."""
    flattened: list[dict[str, Any]] = []
    bias = event.get("eth_bias")
    rec = str(advisory.get("recommendation") or "hold").lower()
    per = advisory.get("per_position") or []
    flatten_ids: set[int] = set()
    if rec == "flatten":
        for p in open_positions:
            flatten_ids.add(int(p["id"]))
    for item in per:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").lower()
        if action == "flatten" and item.get("position_id") is not None:
            flatten_ids.add(int(item["position_id"]))

    for pos in open_positions:
        pid = int(pos["id"])
        contradict = bias_contradicts_side(bias, str(pos.get("side")))
        if not (contradict or pid in flatten_ids):
            continue
        exit_c = _side_exit_cents(pos)
        if exit_c is None:
            continue
        _try_live_close(pos, exit_c)
        closed = paper.flatten_position_early(
            pid,
            exit_side_cents=exit_c,
            reason=f"macro_flatten sev={event.get('severity')} bias={bias}",
        )
        if closed:
            flattened.append(closed)
            logger.info(
                "Macro flatten pos=%s %s %s @ %.1f¢ pnl=$%+.2f",
                pid,
                closed.get("side"),
                closed.get("market_ticker"),
                exit_c,
                float(closed.get("pnl_usd") or 0),
            )
    return flattened


def run_macro_pulse(event: dict[str, Any]) -> dict[str, Any] | None:
    """Run advisory pulse for a classified high-severity event."""
    if int(event.get("severity") or 0) < bot_config.MACRO_PULSE_MIN_SEVERITY:
        return None

    open_positions = paper.get_open_positions()
    position_detail = paper.format_positions_text()

    parts = [
        f"Headline: {event.get('title')}",
        f"Severity: {event.get('severity')} | Bias: {event.get('eth_bias')}",
        f"Impact: {event.get('eth_impact_summary')}",
    ]
    if position_detail:
        parts.append(f"Open positions:\n{position_detail}")
    else:
        parts.append("Open positions: none")

    advisory: dict[str, Any]
    if config.ANTHROPIC_API_KEY:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        try:
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=768,
                system=PULSE_SYSTEM,
                messages=[{"role": "user", "content": "\n".join(parts)}],
            )
            raw = ""
            for block in response.content:
                if block.type == "text":
                    raw += block.text
            try:
                advisory = _extract_json(raw)
            except json.JSONDecodeError:
                advisory = {
                    "recommendation": "hold",
                    "rationale": raw[:500],
                    "per_position": [],
                }
        except Exception:
            logger.exception("Macro pulse API failed — using mechanical bias only")
            advisory = {
                "recommendation": "hold",
                "rationale": "LLM pulse unavailable; using mechanical bias check.",
                "per_position": [],
            }
    else:
        advisory = {
            "recommendation": "hold",
            "rationale": "No API key; mechanical bias check only.",
            "per_position": [],
        }

    # Mechanical override: high-sev contradicting bias → flatten recommendation.
    if any(
        bias_contradicts_side(event.get("eth_bias"), str(p.get("side")))
        for p in open_positions
    ):
        if str(advisory.get("recommendation") or "hold") == "hold":
            advisory["recommendation"] = "flatten"
            note = (
                f"Mechanical: bias {event.get('eth_bias')} contradicts open side(s)."
            )
            advisory["rationale"] = (
                f"{note} {advisory.get('rationale') or ''}"
            ).strip()

    text_summary = str(advisory.get("rationale") or "").strip()
    rec = str(advisory.get("recommendation") or "hold")
    if text_summary:
        text_summary = f"{rec}: {text_summary}"

    pulse_row = store.insert_pulse(
        event_id=int(event["id"]),
        open_positions=open_positions,
        advisory=advisory,
        text_summary=text_summary,
    )

    flattened = maybe_flatten_contradicted(event, open_positions, advisory)

    try:
        notify.send_macro_pulse_alert(
            event, advisory, text_summary, flattened=flattened
        )
    except Exception:
        logger.exception("Macro pulse notify failed")

    return pulse_row
