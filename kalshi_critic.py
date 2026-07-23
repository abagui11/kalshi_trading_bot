"""Kalshi-specific rationale fact checks (coin-flip, mid-richness, soft HTF ack)."""

from __future__ import annotations

import re
from typing import Any

from critic import AuditFinding, rationale_acknowledges_conflicts
from models import Suggestion
from patterns.market_structure_state import alignment_tag

_COIN_FLIP_RE = re.compile(
    r"(?i)("
    r"\bcoin\s*flip\b|\bcoinflip\b|nearly\s+a\s+coin|"
    r"no\s+directional\s+case|"
    r"needs?\s+(?:to\s+)?(?:rally|drop|move)\s*(?:~|about|approximately)?\s*"
    r"0\.0[0-4]\d*\s*%|"
    r"needs?\s+(?:a\s+)?(?:~|about)?\s*0\.0[0-4]\d*\s*%"
    r")"
)

_OVERPRICED_CENTS_RE = re.compile(
    r"(?i)\b(?:overpriced|rich(?:er)?|cheap(?:er)?|edge)\b[^.]{0,40}?"
    r"(\d+(?:\.\d+)?)\s*(?:¢|cents?)"
)

_SETTLE_DISTINCTION_RE = re.compile(
    r"(?i)(window\s+average|reference\s+average|not\s+just\s+the\s+strike|"
    r"settlement|BRTI|resolves?\s+on)"
)

_KALSHI_RULES_CITE_RE = re.compile(
    r"(?i)\b("
    r"KalshiRules|never\s+buy\s*>?\s*55|never\s+chase|"
    r"last\s*3\s*m|lottery\s+ticket|intended\s+limit|"
    r"session:\s*(us_|asia|weekend)|prefer\s*≤?\s*50"
    r")\b"
)


def check_kalshi_rationale(
    text: str,
    suggestion: Suggestion,
    *,
    model_fair_yes_cents: float | None = None,
    yes_mid_cents: float | None = None,
    fair_tol_cents: float = 3.0,
    htf_bias: str | None = None,
) -> list[AuditFinding]:
    """Deterministic Kalshi checks. Critical findings trigger critic retry/downgrade."""
    findings: list[AuditFinding] = []
    body = text or ""
    action = suggestion.action

    if action in ("spot_buy", "spot_sell", "deriv_buy", "deriv_sell"):
        if _COIN_FLIP_RE.search(body):
            findings.append(
                AuditFinding(
                    code="KALSHI_COIN_FLIP_RATIONALE",
                    message=(
                        "Trade action with self-described coin-flip / tiny required move "
                        "in rationale — no real directional case."
                    ),
                    severity="critical",
                )
            )

        if not _KALSHI_RULES_CITE_RE.search(body):
            findings.append(
                AuditFinding(
                    code="KALSHI_MISSING_RULES_CITE",
                    message=(
                        "Trade rationale must explicitly cite Custom KalshiRules "
                        "(session, entry ≤55¢ / intended limit, block/execute, never chase)."
                    ),
                    severity="critical",
                )
            )

        # Soft HTF: counter-structure requires acknowledgment in rationale.
        if htf_bias in ("bull", "bear"):
            side = (
                "YES"
                if action in ("spot_buy", "deriv_buy")
                else "NO"
            )
            if alignment_tag(side, htf_bias) == "counter_htf":
                if not rationale_acknowledges_conflicts(body):
                    findings.append(
                        AuditFinding(
                            code="KALSHI_COUNTER_HTF_UNACKNOWLEDGED",
                            message=(
                                f"Trade {side} fades HTF {htf_bias} without acknowledging "
                                "the conflict (soft HTF policy)."
                            ),
                            severity="critical",
                        )
                    )

        # Noted settlement vs strike distinction but still traded without resolving it.
        if _SETTLE_DISTINCTION_RE.search(body) and re.search(
            r"(?i)traded\s+anyway|anyway\.|despite\s+that", body
        ):
            findings.append(
                AuditFinding(
                    code="KALSHI_SETTLEMENT_IGNORED",
                    message=(
                        "Rationale notes settlement/window-average vs strike distinction "
                        "but does not reconcile it with the trade."
                    ),
                    severity="critical",
                )
            )

        # Require explicit chart-to-chart language on trades.
        if not re.search(r"(?i)\bon\s+h4\b", body) or not re.search(
            r"(?i)\bon\s+(h1|m5|m15)\b", body
        ):
            findings.append(
                AuditFinding(
                    code="KALSHI_MISSING_CHART_COMPARE",
                    message=(
                        "Trade rationale must compare charts explicitly "
                        "(On H4 …; on H1/M5 …)."
                    ),
                    severity="critical",
                )
            )

    if (
        model_fair_yes_cents is not None
        and yes_mid_cents is not None
        and body
    ):
        model_edge = float(model_fair_yes_cents) - float(yes_mid_cents)
        for m in _OVERPRICED_CENTS_RE.finditer(body):
            claimed = float(m.group(1))
            if abs(model_edge) < fair_tol_cents and claimed >= 5.0:
                findings.append(
                    AuditFinding(
                        code="KALSHI_FABRICATED_EDGE",
                        message=(
                            f"Rationale claims ~{claimed:.1f}¢ mid richness/edge but "
                            f"model fair {model_fair_yes_cents:.1f}¢ vs mid "
                            f"{yes_mid_cents:.1f}¢ (edge {model_edge:+.1f}¢)."
                        ),
                        severity="critical",
                    )
                )
                break

    return findings


def findings_to_json(findings: list[AuditFinding]) -> list[dict[str, Any]]:
    return [f.to_dict() for f in findings]
