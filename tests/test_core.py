"""Regression checks for financial dates, data quality, and immutable local records.

Run from the project root: python -m unittest discover -s tests -v
No live HTTP calls are made. All writes are redirected to a temporary directory.
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import market
import storage


def bj(value):
    return datetime.fromisoformat(value + "+08:00")


def series(code, values):
    return {
        "code": code,
        "rows": [{"date": day, "close": price} for day, price in values],
        "source_url": "https://example.invalid/test-prices/" + code,
        "basis": "test-only adjusted prices",
    }


def forecast(**changes):
    value = {
        "id": "test-forecast",
        "code": "sh512480",
        "registered_at": "2026-10-12T12:00:00+08:00",
        "base_date": "2026-10-09",
        "eligible_from": "2026-10-12",
        "start_date": None,
        "due_date": None,
        "horizon_days": 7,
        "direction": "outperform",
        "benchmark": market.BENCH,
        "method": "momentum-v2",
        "evaluation": None,
    }
    value.update(changes)
    return value


def quote_text(stamp, value=101.0, previous=100.0, percent=1.0):
    fields = [""] * 33
    fields[1] = "test ETF"
    fields[3] = str(value)
    fields[4] = str(previous)
    fields[30] = stamp
    fields[32] = str(percent)
    return 'v_sh512480="' + "~".join(fields) + '";'


class ForecastEvaluationTests(unittest.TestCase):
    def test_noon_forecast_excludes_returns_known_before_registration(self):
        item = forecast()
        histories = {
            item["code"]: series(item["code"], [
                ("2026-10-09", 100.0),
                ("2026-10-12", 110.0),
                ("2026-10-19", 105.0),
            ]),
            market.BENCH: series(market.BENCH, [
                ("2026-10-09", 100.0),
                ("2026-10-12", 100.0),
                ("2026-10-19", 100.0),
            ]),
        }
        market.evaluate([item], histories, bj("2026-10-19T16:00:00"))
        self.assertEqual(item["start_date"], "2026-10-12")
        self.assertEqual(item["due_date"], "2026-10-19")
        self.assertAlmostEqual(item["evaluation"]["return_pct"], -4.5455, places=4)
        self.assertFalse(item["evaluation"]["hit"])

    def test_after_close_forecast_starts_on_next_available_close(self):
        item = forecast(
            registered_at="2026-10-12T16:00:00+08:00",
            eligible_from="2026-10-13",
        )
        dates = ["2026-10-12", "2026-10-13", "2026-10-20"]
        histories = {
            item["code"]: series(item["code"], list(zip(dates, [110.0, 120.0, 126.0]))),
            market.BENCH: series(market.BENCH, list(zip(dates, [100.0] * 3))),
        }
        market.evaluate([item], histories, bj("2026-10-20T16:00:00"))
        self.assertEqual(item["start_date"], "2026-10-13")
        self.assertEqual(item["due_date"], "2026-10-20")
        self.assertEqual(item["evaluation"]["return_pct"], 5.0)

    def test_forecast_is_not_scored_before_due_date(self):
        item = forecast()
        histories = {
            item["code"]: series(item["code"], [("2026-10-12", 110.0)]),
            market.BENCH: series(market.BENCH, [("2026-10-12", 100.0)]),
        }
        market.evaluate([item], histories, bj("2026-10-12T16:00:00"))
        self.assertEqual(item["start_date"], "2026-10-12")
        self.assertEqual(item["due_date"], "2026-10-19")
        self.assertIsNone(item["evaluation"])

    def test_missing_due_price_cannot_be_replaced_by_later_price(self):
        item = forecast(start_date="2026-10-12", due_date="2026-10-19")
        histories = {
            item["code"]: series(item["code"], [
                ("2026-10-12", 100.0), ("2026-10-21", 120.0),
            ]),
            market.BENCH: series(market.BENCH, [
                ("2026-10-12", 100.0),
                ("2026-10-19", 100.0),
                ("2026-10-21", 100.0),
            ]),
        }
        market.evaluate([item], histories, bj("2026-10-21T16:00:00"))
        self.assertIsNone(item["evaluation"])

    def test_weekend_due_date_uses_first_benchmark_session(self):
        item = forecast(
            registered_at="2026-10-08T16:00:00+08:00",
            eligible_from="2026-10-09",
            start_date="2026-10-09",
            due_date="2026-11-08",
            horizon_days=30,
        )
        histories = {
            item["code"]: series(item["code"], [
                ("2026-10-09", 100.0), ("2026-11-09", 102.0),
            ]),
            market.BENCH: series(market.BENCH, [
                ("2026-10-09", 100.0), ("2026-11-09", 101.0),
            ]),
        }
        market.evaluate([item], histories, bj("2026-11-09T16:00:00"))
        self.assertEqual(item["evaluation"]["date"], "2026-11-09")
        self.assertEqual(item["evaluation"]["excess_pct"], 1.0)
        self.assertTrue(item["evaluation"]["hit"])

    def test_missing_first_entry_price_does_not_shift_entry_later(self):
        item = forecast()
        histories = {
            item["code"]: series(item["code"], [("2026-10-13", 120.0)]),
            market.BENCH: series(market.BENCH, [
                ("2026-10-12", 100.0), ("2026-10-13", 100.0),
            ]),
        }
        market.evaluate([item], histories, bj("2026-10-13T16:00:00"))
        self.assertIsNone(item["start_date"])
        self.assertIsNone(item["evaluation"])

    def test_scored_result_remains_frozen_on_later_refresh(self):
        evaluation = {"date": "2026-10-19", "return_pct": 2.0, "hit": True}
        item = forecast(start_date="2026-10-12", due_date="2026-10-19", evaluation=evaluation)
        before = copy.deepcopy(item)
        self.assertEqual(market.evaluate([item], {}, bj("2026-10-23T16:00:00")), 0)
        self.assertEqual(item, before)


class AccuracyCoverageTests(unittest.TestCase):
    def test_overdue_missing_data_is_counted_in_coverage_not_hits(self):
        items = [
            forecast(id="hit", start_date="2026-10-12", due_date="2026-10-19", evaluation={"hit": True}),
            forecast(id="missing", start_date="2026-10-12", due_date="2026-10-19"),
            forecast(id="not-due", start_date="2026-10-20", due_date="2026-10-27"),
            forecast(id="not-started"),
        ]
        result = storage.statistics(items, bj("2026-10-20T16:00:00"))
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["due_unavailable"], 1)
        self.assertEqual(result["coverage_pct"], 50.0)
        self.assertEqual(result["accuracy_pct"], 100.0)
        self.assertEqual(result["awaiting_start"], 1)

    def test_unmatured_samples_do_not_create_accuracy(self):
        item = forecast(start_date="2026-10-20", due_date="2026-10-27")
        result = storage.statistics([item], bj("2026-10-20T16:00:00"))
        self.assertIsNone(result["accuracy_pct"])
        self.assertIsNone(result["coverage_pct"])
        self.assertEqual(result["due_unavailable"], 0)


class MarketDataQualityTests(unittest.TestCase):
    def test_future_quote_beyond_clock_tolerance_is_rejected(self):
        self.assertEqual(
            market.parse_quotes(quote_text("20261012120600"), bj("2026-10-12T12:00:00")),
            [],
        )

    def test_same_day_morning_quote_is_stale_in_afternoon_report(self):
        rows = market.parse_quotes(quote_text("20261012093100"), bj("2026-10-12T16:00:00"))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["stale"])
        self.assertFalse(rows[0]["historical"])
        self.assertEqual(rows[0]["as_of"], "2026-10-12T09:31:00+08:00")

    def test_close_quote_and_lunch_break_quote_are_not_falsely_stale(self):
        for stamp, current in [
            ("20261012150000", "2026-10-12T16:00:00"),
            ("20261012113000", "2026-10-12T12:00:00"),
        ]:
            with self.subTest(stamp=stamp):
                rows = market.parse_quotes(quote_text(stamp), bj(current))
                self.assertEqual(len(rows), 1)
                self.assertFalse(rows[0]["stale"])

    def test_mismatched_observation_dates_are_rejected(self):
        own = series("sh512480", [
            ("2026-10-09", 100.0), ("2026-10-13", 110.0), ("2026-10-14", 120.0),
        ])
        benchmark = series(market.BENCH, [
            ("2026-10-12", 100.0), ("2026-10-13", 110.0), ("2026-10-14", 120.0),
        ])
        with self.assertRaises(ValueError):
            market.momentum(own, "2026-10-14", 2, benchmark)

    def test_matching_observation_dates_produce_expected_return(self):
        own = series("sh512480", [
            ("2026-10-12", 100.0), ("2026-10-13", 110.0), ("2026-10-14", 120.0),
        ])
        self.assertAlmostEqual(market.momentum(own, "2026-10-14", 2, own), 20.0)

    def test_unadjusted_history_cannot_silently_replace_adjusted_history(self):
        raw = {"data": {"sh512480": {"day": [["2026-10-09", "100", "100"]]}}}
        with patch.object(market, "get", return_value=json.dumps(raw)):
            with self.assertRaises(ValueError):
                market.history("sh512480", bj("2026-10-12T16:00:00"))


class IsolatedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="market-assistant-tests-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        for name, value in [("DATA", self.data), ("REPORTS", self.data / "reports")]:
            context = patch.object(storage, name, value)
            context.start()
            self.addCleanup(context.stop)
        clock = patch.object(storage, "now", return_value=bj("2026-10-23T16:00:00"))
        clock.start()
        self.addCleanup(clock.stop)

    @staticmethod
    def portfolio():
        return {
            "as_of": "2026-10-23",
            "snapshot": {
                "day_profit": -10.0,
                "day_return_pct": -1.0,
                "month_profit": -20.0,
                "total_assets_estimate": 1000.0,
            },
            "fund_allocation": [],
            "sector_allocation": [],
            "asset_allocation": [],
        }

    def test_invalid_snapshot_values_are_rejected_without_overwriting(self):
        valid = self.portfolio()
        storage.save_portfolio(valid)
        path = self.data / "portfolio.json"
        original = path.read_bytes()
        cases = [
            ("total_assets_estimate", -1.0),
            ("day_profit", "abc"),
            ("day_return_pct", "1.5"),
            ("month_profit", float("nan")),
            ("total_assets_estimate", float("inf")),
            ("day_profit", True),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=repr(value)):
                # Keep each invalid-input case independent if a previous case fails.
                storage.save_portfolio(valid)
                invalid = self.portfolio()
                invalid["snapshot"][field] = value
                with self.assertRaises(ValueError):
                    storage.save_portfolio(invalid)
                self.assertEqual(path.read_bytes(), original)

    def test_unknown_snapshot_values_can_remain_null(self):
        value = self.portfolio()
        value["snapshot"] = {key: None for key in value["snapshot"]}
        storage.save_portfolio(value)
        self.assertEqual(storage.read(self.data / "portfolio.json"), value)

    def test_future_portfolio_date_is_rejected(self):
        value = self.portfolio()
        value["as_of"] = "2026-10-24"
        with self.assertRaises(ValueError):
            storage.save_portfolio(value)

    def test_report_id_cannot_overwrite_existing_report(self):
        report = {
            "id": "immutable-report",
            "title": "Original research",
            "as_of": "2026-10-23T12:00:00+08:00",
            "summary": "Original conclusion",
            "market": [],
            "outlooks": [],
            "sources": [{"url": "https://example.invalid/source"}],
            "limitations": [],
        }
        path = storage.register_report(report)
        original = path.read_bytes()
        replacement = dict(report, summary="Changed after seeing the outcome")
        with self.assertRaises(ValueError):
            storage.register_report(replacement)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(storage.read(path)["summary"], "Original conclusion")


if __name__ == "__main__":
    unittest.main()
