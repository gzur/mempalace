"""Tests for web_ui.render_operations (/ui/operations chart page)."""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from mempalace import web_ui


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


def test_render_operations_empty_wal(tmp_path, monkeypatch):
    missing = tmp_path / "no-such.jsonl"
    monkeypatch.setattr(web_ui, "_WAL_PATH", missing)

    html = web_ui.render_operations(days=30)

    assert "<h1>operations</h1>" in html
    # chart frame is rendered (empty x-axis still useful as scaffold) — no
    # colored bars, but transparent hover overlays per column are expected so
    # users still get a "no ops" tooltip on hover.
    assert '<svg class="chart"' in html
    assert 'fill="#' not in html  # no colored op_type bars
    assert 'fill="transparent"' in html  # hover overlays present
    # totals block shows zero counts
    assert "0 ops in last 30d" in html
    assert "total" in html and "avg / day" in html


def test_render_operations_buckets_by_day(tmp_path, monkeypatch):
    wal = tmp_path / "wal.jsonl"
    now = _dt.datetime.now()
    _write_wal(
        wal,
        [
            _op(now - _dt.timedelta(days=2), "add_drawer", wing="self", room="r"),
            _op(now - _dt.timedelta(days=2), "diary_write", agent_name="gzur"),
            _op(now - _dt.timedelta(days=1), "add_drawer", wing="self", room="r"),
            _op(now - _dt.timedelta(days=1), "kg_add"),
            _op(now - _dt.timedelta(hours=1), "update_drawer"),
        ],
    )
    monkeypatch.setattr(web_ui, "_WAL_PATH", wal)

    html = web_ui.render_operations(days=7, bucket="day")

    # chart present with at least one bar
    assert '<svg class="chart"' in html
    assert "<rect " in html
    # legend has each op_type
    for t in ("add_drawer", "diary_write", "update_drawer", "kg_add", "delete_drawer"):
        assert t in html
    # totals reflects 5 ops
    assert "5" in html  # total
    # top wing "self" appears (2 add_drawers + 1 update with no wing)
    assert "self" in html


def test_render_operations_respects_days_param(tmp_path, monkeypatch):
    wal = tmp_path / "wal.jsonl"
    now = _dt.datetime.now()
    _write_wal(
        wal,
        [
            # old: 60 days ago — should be filtered out at days=7
            _op(now - _dt.timedelta(days=60), "add_drawer", wing="self"),
            _op(now - _dt.timedelta(days=60), "add_drawer", wing="self"),
            # recent: 1 day ago — should be included
            _op(now - _dt.timedelta(days=1), "add_drawer", wing="self"),
        ],
    )
    monkeypatch.setattr(web_ui, "_WAL_PATH", wal)

    html_7d = web_ui.render_operations(days=7, bucket="day")
    html_90d = web_ui.render_operations(days=90, bucket="day")

    # 7d view: 1 op total
    assert "1 ops in last 7d" in html_7d
    # 90d view: 3 ops total
    assert "3 ops in last 90d" in html_90d


def test_render_operations_skips_malformed_lines(tmp_path, monkeypatch):
    wal = tmp_path / "wal.jsonl"
    now = _dt.datetime.now()
    with open(wal, "w", encoding="utf-8") as f:
        f.write("{not valid json\n")
        f.write(json.dumps(_op(now - _dt.timedelta(hours=1), "add_drawer", wing="x")) + "\n")
        f.write('{"timestamp": "not-an-iso", "operation": "x"}\n')
        f.write("\n")
        f.write(json.dumps(_op(now - _dt.timedelta(hours=2), "diary_write")) + "\n")
    monkeypatch.setattr(web_ui, "_WAL_PATH", wal)

    html = web_ui.render_operations(days=1, bucket="hour")

    # Only the two parseable, in-range lines survive
    assert "2 ops in last 1d" in html


def test_render_operations_auto_bucket_short_range(tmp_path, monkeypatch):
    monkeypatch.setattr(web_ui, "_WAL_PATH", tmp_path / "absent.jsonl")
    # days=1, no bucket → hour
    html = web_ui.render_operations(days=1)
    assert "bucket=hour" in html
    # days=30, no bucket → day
    html = web_ui.render_operations(days=30)
    assert "bucket=day" in html


def test_render_operations_clamps_days(tmp_path, monkeypatch):
    monkeypatch.setattr(web_ui, "_WAL_PATH", tmp_path / "absent.jsonl")
    html = web_ui.render_operations(days=99999)
    # ceiling is 365
    assert "last 365d" in html
    html = web_ui.render_operations(days=0)
    assert "last 1d" in html


@pytest.mark.parametrize(
    "days,bucket,expected_substr",
    [
        (30, "day", "last 30d"),
        (7, "day", "last 7d"),
        (1, "hour", "last 1d"),
    ],
)
def test_render_operations_meta_line(tmp_path, monkeypatch, days, bucket, expected_substr):
    monkeypatch.setattr(web_ui, "_WAL_PATH", tmp_path / "absent.jsonl")
    html = web_ui.render_operations(days=days, bucket=bucket)
    assert expected_substr in html
    assert f"bucket={bucket}" in html
