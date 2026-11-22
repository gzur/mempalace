"""Tests for ``mempalace hook-preview``."""

import argparse
import json

from mempalace import cli


def _args(**kw):
    base = dict(prompt="", file=None, strategy="union", recency=None, json=False, palace="/tmp/p")
    base.update(kw)
    return argparse.Namespace(**base)


def test_hook_preview_prints_context(monkeypatch, capsys):
    calls = {}

    def fake_search(query, **kw):
        calls.update(kw, query=query)
        return {
            "results": [
                {
                    "drawer_id": "d1",
                    "similarity": 0.7,
                    "source_date": "2026-10-23T10:00:00",
                    "source_file": "s.jsonl",
                    "text": "VICE-2192 made the nightly job blocking",
                }
            ]
        }

    monkeypatch.setattr("mempalace.searcher.search_memories", fake_search)
    assert cli.cmd_hook_preview(_args(prompt="Did VICE-2192 land?", recency=0.1)) == 0
    out = capsys.readouterr().out
    assert "2026-10-23 (newest) · s.jsonl · d1" in out
    assert calls["candidate_strategy"] == "union"
    assert calls["max_distance"] == 0.0
    assert calls["recency_weight"] == 0.1


def test_hook_preview_json_reads_file(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("mempalace.searcher.search_memories", lambda q, **kw: {"results": []})
    f = tmp_path / "brief.md"
    f.write_text("Task: VICE-1 do a thing now please.", encoding="utf-8")
    cli.cmd_hook_preview(_args(file=str(f), json=True))
    info = json.loads(capsys.readouterr().out)
    assert info["query"].startswith("Task: VICE-1")
    assert info["context"] == ""
