"""Tests for short-horizon trigger, HTF veto, shadow tags."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import kalshi_triggers
from patterns.market_context import MarketContext


def _ctx(tags: list[str]) -> MarketContext:
    return MarketContext(
        range_24h=None,
        is_ranging=False,
        range_break=None,
        spot=100.0,
        zone_snapshot=None,
        setup_state=None,
        order_blocks=[],
        m5_sfps=[],
        setup_tags=tags,
    )


class TestShortHorizonTrigger(unittest.TestCase):
    def test_yes_when_through_and_momentum_up(self) -> None:
        tr = kalshi_triggers.short_horizon_trigger(
            spot=100.10,
            strike=100.0,
            yes_mid_cents=48.0,
            prior_5m_ret_pct=0.12,  # <0.25% so not chase-blocked
            prior_15m_ret_pct=0.2,
            market_context=_ctx(["htf_bull"]),
        )
        self.assertEqual(tr.side, "YES")
        self.assertFalse(kalshi_triggers.htf_vetoes(tr.side, tr.htf_bias))

    def test_no_when_below_and_momentum_down(self) -> None:
        tr = kalshi_triggers.short_horizon_trigger(
            spot=99.90,
            strike=100.0,
            yes_mid_cents=55.0,  # NO entry = 45¢
            prior_5m_ret_pct=-0.1,
            prior_15m_ret_pct=-0.2,
            market_context=_ctx(["htf_bear"]),
        )
        self.assertEqual(tr.side, "NO")

    def test_skips_rich_ticket(self) -> None:
        tr = kalshi_triggers.short_horizon_trigger(
            spot=100.20,
            strike=100.0,
            yes_mid_cents=70.0,  # YES too rich (>55)
            prior_5m_ret_pct=0.12,
            prior_15m_ret_pct=0.3,
        )
        self.assertIsNone(tr.side)
        self.assertIn("55", tr.reason)

    def test_skips_large_m5_impulse_chase(self) -> None:
        tr = kalshi_triggers.short_horizon_trigger(
            spot=100.10,
            strike=100.0,
            yes_mid_cents=45.0,
            prior_5m_ret_pct=0.30,  # ≥0.25% — never chase
            prior_15m_ret_pct=0.40,
        )
        self.assertIsNone(tr.side)
        self.assertIn("retrace", tr.reason.lower())

    def test_compose_rationale_cites_kalshirules(self) -> None:
        text = kalshi_triggers.compose_kalshi_rules_rationale(
            session="us_rth",
            trigger_reason="test trigger",
            side="YES",
            yes_mid_cents=46.0,
            entry_cents=43.0,
            limit_cents=43.0,
            fair_cents=58.0,
            edge_cents=12.0,
            gate_outcome="pass_fib",
            ict_bias="long",
            ict_rationale="M5 OB fib",
            minutes_left=12.0,
        )
        self.assertIn("KalshiRules", text)
        self.assertIn("session", text.lower())

    def test_missing_rules_cite_flagged(self) -> None:
        import kalshi_critic
        from models import Suggestion

        s = Suggestion(
            action="spot_buy",
            size=0,
            entry=100.0,
            stop_loss=99.0,
            take_profits=[101.0],
            rationale="Price above strike with mild momentum.",
            product_id="BTC-USD",
        )
        codes = {f.code for f in kalshi_critic.check_kalshi_rationale(s.rationale, s)}
        self.assertIn("KALSHI_MISSING_RULES_CITE", codes)

    def test_htf_veto_bull_vs_no(self) -> None:
        self.assertTrue(kalshi_triggers.htf_vetoes("NO", "bull"))
        self.assertFalse(kalshi_triggers.htf_vetoes("YES", "bull"))
        self.assertFalse(kalshi_triggers.htf_vetoes("NO", "mixed"))

    def test_shadow_tags(self) -> None:
        reasons = kalshi_triggers.shadow_skip_reasons(
            side="YES",
            entry_cents=62.0,
            through_strike_pct=0.1,
            momentum_pct=-0.2,
            gate_outcome="fail",
            htf_bias="bear",
            fair_yes_cents=51.0,
            yes_mid_cents=50.0,
            min_edge=8.0,
        )
        self.assertIn("rich_ticket", reasons)
        self.assertIn("against_momentum", reasons)
        self.assertIn("gate_fail", reasons)
        self.assertIn("htf_conflict", reasons)
        self.assertIn("coin_flip_gap", reasons)


if __name__ == "__main__":
    unittest.main()
