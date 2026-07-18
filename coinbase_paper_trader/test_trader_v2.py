import unittest
from datetime import datetime, timedelta, timezone

import trader


def make_candles(start_price=100.0, returns=None, volume=1000.0):
    candles = []
    price = start_price
    timestamp = 1_700_000_000
    returns = returns or [0.001, -0.001] * 20
    for change in returns:
        opened = price
        close = max(0.01, opened * (1.0 + change))
        high = max(opened, close) * 1.001
        low = min(opened, close) * 0.999
        candles.append([timestamp, low, high, opened, close, volume])
        price = close
        timestamp += 300
    return candles


class TradingV2SpecTests(unittest.TestCase):
    def test_oi_validation_boosts_and_penalizes_momentum(self):
        strong = {
            "momentum_runner_score": 80.0,
            "momentum_runner_features": {},
            "derivatives_metrics": {"open_interest_change_24h_pct": 12.5},
        }
        trader.apply_oi_validation_to_momentum(strong)
        self.assertEqual(strong["momentum_runner_score"], 90.0)
        self.assertEqual(strong["momentum_runner_features"]["oi_validation"], "confirmed")

        weak = {
            "momentum_runner_score": 80.0,
            "momentum_runner_features": {},
            "derivatives_metrics": {"open_interest_change_24h_pct": 2.0},
        }
        trader.apply_oi_validation_to_momentum(weak)
        self.assertEqual(weak["momentum_runner_score"], 40.0)
        self.assertEqual(weak["momentum_runner_features"]["oi_validation"], "penalized")

    def test_liquidation_cascade_requires_provider_metrics_and_low_ball_limit(self):
        candles = make_candles(returns=([0.001, -0.001] * 18) + [-0.065])
        product = {
            "product_id": "TEST-USD",
            "price": candles[-1][4],
            "derivatives_metrics": {
                "long_liquidation_percentile_7d": 99,
                "open_interest_change_24h_pct": -12,
            },
        }
        result = trader.detect_liquidation_cascade_signal(product, candles)
        self.assertEqual(result["liquidation_score"], 100.0)
        self.assertLess(result["features"]["liquidation_limit_price"], product["price"])
        self.assertTrue(result["features"]["limit_only"])

        product["derivatives_metrics"] = {}
        missing = trader.detect_liquidation_cascade_signal(product, candles)
        self.assertEqual(missing["liquidation_score"], 0.0)

    def test_cvd_divergence_requires_spot_buying_against_perp_selling(self):
        candles = make_candles(returns=[0.0002, -0.0001, 0.0001, -0.0002] * 5)
        product = {
            "product_id": "TEST-USD",
            "derivatives_metrics": {
                "spot_cvd_slope": 3.0,
                "spot_buy_dominance": 0.62,
                "perp_buy_dominance": 0.42,
            },
        }
        result = trader.detect_cvd_divergence_signal(product, candles)
        self.assertGreaterEqual(result["cvd_score"], trader.CVD_DIVERGENCE_MIN_SCORE)
        self.assertTrue(result["features"]["cross_exchange_confirmed"])

    def test_funding_gate_blocks_only_when_rate_is_available_and_crowded(self):
        ok = trader.derivatives_regime_gate({"derivatives_metrics": {}})
        self.assertTrue(ok["ok"])
        self.assertFalse(ok["available"])

        blocked = trader.derivatives_regime_gate({
            "derivatives_metrics": {"funding_rate_8h_pct": trader.MAX_FUNDING_RATE_8H_PCT + 0.01}
        })
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["available"])

    def test_dynamic_distribution_marks_extreme_range_distribution(self):
        hourly = []
        timestamp = 1_700_000_000
        for index in range(14 * 24):
            opened = 100.0
            close = 100.4 if index % 5 else 99.8
            volume = 1000.0 if index % 5 else 700.0
            hourly.append([timestamp, 99.5, 100.8, opened, close, volume])
            timestamp += 3600

        analysis = {
            "score": 40.0,
            "score_components": {"legacy": 40},
            "reasons": [],
            "down_volume_ratio_4h": 0.70,
            "upper_wick_rejections_4h": 18,
            "price_change_4h": -1.0,
            "obv_pressure_4h": -8.0,
        }
        updated = trader.apply_dynamic_distribution_baseline(analysis, hourly)
        self.assertEqual(updated["phase"], "DISTRIBUTION")
        self.assertTrue(updated["would_block"])
        self.assertIn("dynamic_down_volume_z", updated)

    def test_stagnant_exit_detects_four_quiet_15m_bars_with_fading_volume(self):
        candles = []
        timestamp = 1_700_000_000
        for block in range(18):
            for _ in range(3):
                opened = 100.0
                close = 100.3 if block % 2 else 99.7
                candles.append([timestamp, 99.0, 101.0, opened, close, 1000.0])
                timestamp += 300
        for _ in range(4):
            for _ in range(3):
                candles.append([timestamp, 99.95, 100.02, 100.0, 99.98, 120.0])
                timestamp += 300

        pos = {
            "entry_price": 100.0,
            "entry_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=95)).isoformat(),
        }
        reason = trader._stagnant_position_reason(pos, 99.9, candles)
        self.assertTrue(reason.startswith("STAGNANT_CAPITAL_"))

    def test_quality_near_miss_vvv_like_setup_becomes_starter_only(self):
        old_min_signal = trader.MIN_SIGNAL_SCORE
        trader.MIN_SIGNAL_SCORE = 70.0
        product = {
            "product_id": "VVV-USD",
            "price": 11.42,
            "score": 60.0,
            "pre_breakout_score": 57.0,
            "orb_score": 29.0,
            "bollinger_score": 0.0,
            "wedge_score": 0.0,
            "momentum_runner_score": 0.0,
            "price_change_24h": 6.88,
            "price_change_1h": 1.13,
            "dollar_volume_24h": 7_670_158.83,
            "obv": {"obv_pressure_pct": 50.23, "up_volume_ratio": 0.62},
            "distribution_shadow": {
                "score": 15.0,
                "phase": "MARKUP",
                "would_block": False,
                "reasons": [],
            },
        }
        try:
            signal = trader.select_entry_signal(product)
            self.assertTrue(signal["eligible"])
            self.assertTrue(signal["starter_only"])
            self.assertEqual(signal["score"], 68.0)
            self.assertIn("quality_near_miss_starter", signal["features"])
        finally:
            trader.MIN_SIGNAL_SCORE = old_min_signal

    def test_less_trades_mode_caps_medium_quality_size_and_slots(self):
        signal = {"score": 78.0, "consensus_count": 2}
        size, reasons = trader._less_trades_position_size(signal, 1000.0)
        self.assertEqual(size, 400.0)
        self.assertTrue(any("less-trades cap" in reason for reason in reasons))

        strong = {"score": 95.0, "consensus_count": 3}
        size, reasons = trader._less_trades_position_size(strong, 1000.0)
        self.assertEqual(size, 1000.0)
        self.assertEqual(reasons, [])

        active = {"A": {"allocated_usd": 100.0}, "B": {"allocated_usd": 100.0}}
        self.assertTrue(trader._budget_is_full(active, {}, next_size=100.0))

    def test_initial_stop_and_trail_have_breathing_floor(self):
        product = {"product_id": "TEST-USD", "price": 100.0}
        signal = {
            "strategy": "24H_MOMENTUM_VOLUME",
            "score": 90.0,
            "features": {},
            "confidence_level": "HIGH",
            "consensus_count": 3,
            "confirming_strategies": ["24H_MOMENTUM_VOLUME", "CANDLE_BREAKOUT", "OPENING_RANGE_BREAKOUT"],
        }
        structure = {"ok": True, "stop_loss": 98.0, "support_level": 99.0, "support_floor": 98.0}
        positions = {}
        trader._open_position_from_entry(positions, product, signal, structure, 100.0, 100.0, {}, [])
        self.assertLessEqual(positions["TEST-USD"]["current_trailing_stop"], 93.5)

        trail_pct, _ = trader.compute_trailing_stop_pct("TEST-USD", 100.0, 100.0, {"TEST-USD": 0.1})
        self.assertGreaterEqual(trail_pct, trader.MIN_BREATHING_TRAIL_PCT)


if __name__ == "__main__":
    unittest.main()
