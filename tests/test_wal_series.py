"""Unit tests for the pure WAL load/bucket/fill helpers.

The same helpers back ``web_ui.render_operations`` and the JSON endpoint
``/api/v1/ops/series``. Tests here only cover the helpers; the rendered
chart is exercised by ``tests/test_web_ui.py``.
"""

from __future__ import annotations

import datetime as _dt
import json

from mempalace import wal_series


def _write_wal(path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _op(ts: _dt.datetime, operation: str, **params) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "operation": operation,
        "params": params,
        "result": None,
    }


class TestLoadWalOps:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert wal_series.load_wal_ops(tmp_path / "absent.jsonl", since_days=7) == []

    def test_skips_malformed_lines_without_crashing(self, tmp_path):
        wal = tmp_path / "wal.jsonl"
        now = _dt.datetime(2026, 5, 21, 12, 0, 0)
        with open(wal, "w", encoding="utf-8") as f:
            f.write(json.dumps(_op(now, "add_drawer")) + "\n")
            f.write("not-json-at-all\n")
            f.write(json.dumps({"timestamp": "garbage", "operation": "x"}) + "\n")
            f.write(json.dumps(_op(now, "diary_write")) + "\n")

        ops = wal_series.load_wal_ops(wal, since_days=30, now=now)
        # The two good rows survive; the bad ones are dropped silently.
        assert {o["operation"] for o in ops} == {"add_drawer", "diary_write"}

    def test_filters_by_cutoff_boundary(self, tmp_path):
        wal = tmp_path / "wal.jsonl"
        now = _dt.datetime(2026, 5, 21, 12, 0, 0)
        # 30 days back exactly at midnight = on the cutoff for days=30.
        before = now - _dt.timedelta(days=40)
        inside = now - _dt.timedelta(days=10)
        _write_wal(wal, [_op(before, "add_drawer"), _op(inside, "add_drawer")])

        ops = wal_series.load_wal_ops(wal, since_days=30, bucket="day", now=now)

        assert len(ops) == 1
        assert ops[0]["_dt"] == inside


class TestBucketOps:
    def test_daily_grouping(self):
        d1 = _dt.datetime(2026, 5, 20, 9, 0)
        d2 = _dt.datetime(2026, 5, 21, 11, 0)
        ops = [
            {"_dt": d1, "operation": "add_drawer"},
            {"_dt": d1, "operation": "diary_write"},
            {"_dt": d2, "operation": "add_drawer"},
        ]
        binned = wal_series.bucket_ops(ops, bucket="day")
        assert list(binned.keys()) == ["2026-05-20", "2026-05-21"]
        assert binned["2026-05-20"]["add_drawer"] == 1
        assert binned["2026-05-20"]["diary_write"] == 1
        assert binned["2026-05-21"]["add_drawer"] == 1

    def test_unknown_op_falls_into_other(self):
        ops = [{"_dt": _dt.datetime(2026, 5, 21), "operation": "wild"}]
        binned = wal_series.bucket_ops(ops, bucket="day")
        assert binned["2026-05-21"]["_other"] == 1


class TestFillBucketRange:
    def test_daily_fill_inserts_empty_buckets(self):
        now = _dt.datetime(2026, 5, 21, 12, 0)
        from collections import OrderedDict

        binned: OrderedDict = OrderedDict()
        binned["2026-05-21"] = {t: 0 for t in wal_series.OP_TYPES} | {
            "_other": 0,
            "add_drawer": 5,
        }
        filled = wal_series.fill_bucket_range(binned, bucket="day", days=3, now=now)
        assert list(filled.keys()) == ["2026-05-19", "2026-05-20", "2026-05-21"]
        assert filled["2026-05-21"]["add_drawer"] == 5
        assert filled["2026-05-20"]["add_drawer"] == 0

    def test_hourly_fill_yields_days_times_24_buckets(self):
        now = _dt.datetime(2026, 5, 21, 12, 0)
        from collections import OrderedDict

        filled = wal_series.fill_bucket_range(OrderedDict(), bucket="hour", days=2, now=now)
        assert len(filled) == 48


class TestAutoBucket:
    def test_short_range_picks_hour(self):
        assert wal_series.auto_bucket(1) == "hour"
        assert wal_series.auto_bucket(2) == "hour"

    def test_long_range_picks_day(self):
        assert wal_series.auto_bucket(7) == "day"
        assert wal_series.auto_bucket(365) == "day"


class TestSeriesForChart:
    def test_shape_for_json_consumer(self, tmp_path):
        wal = tmp_path / "wal.jsonl"
        now = _dt.datetime(2026, 5, 21, 12, 0)
        _write_wal(wal, [_op(now - _dt.timedelta(hours=2), "add_drawer", wing="w")])

        result = wal_series.series_for_chart(path=wal, days=1, bucket="hour", now=now)

        assert result["days"] == 1
        assert result["bucket"] == "hour"
        assert result["total_ops"] == 1
        assert result["op_types"] == list(wal_series.OP_TYPES)
        assert len(result["buckets"]) == 24
        # The bucket containing the op (2 hours before "now") got the count.
        keys = [b["key"] for b in result["buckets"]]
        assert keys[-3].endswith(" 10:00")
        assert result["buckets"][-3]["counts"]["add_drawer"] == 1
