"""Tests for the UserPromptSubmit hook (bead self-d1t).

The hook injects a tiny smoke-signal block listing candidate drawer
IDs from a fast mempalace search. We exercise:

* gate paths — meta-command, short prompt, opt-out, no mempalace MCP
* happy path — relevant search → ``additionalContext`` with locators
* empty-match path — similarity-filtered to nothing → ``{}``
* fail-open paths — daemon down, malformed JSON, HTTP timeout
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error

import pytest

from mempalace import hooks_cli


# ── helpers ───────────────────────────────────────────────────────────


@pytest.fixture
def stub_palace_root(monkeypatch, tmp_path):
    """Pretend the palace root exists so the hook doesn't bail early."""
    monkeypatch.setattr(hooks_cli, "_palace_root_exists", lambda: True)
    monkeypatch.setattr(hooks_cli, "STATE_DIR", tmp_path / "state")
    return tmp_path


@pytest.fixture
def cwd_with_mempalace(monkeypatch):
    """Make _cwd_has_mempalace_mcp return True for any cwd."""
    monkeypatch.setattr(hooks_cli, "_cwd_has_mempalace_mcp", lambda *_a, **_kw: True)


@pytest.fixture
def captured_output(monkeypatch):
    """Capture whatever the hook writes via _output."""
    captured: dict[str, dict | None] = {"payload": None}

    def fake_output(data):
        captured["payload"] = data

    monkeypatch.setattr(hooks_cli, "_output", fake_output)
    return captured


# ── _cwd_opted_out ────────────────────────────────────────────────────


def test_opt_out_returns_false_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks_cli, "_OPT_OUT_PATH", tmp_path / "missing.json")
    assert hooks_cli._cwd_opted_out("/anywhere") is False


def test_opt_out_returns_true_when_cwd_listed(monkeypatch, tmp_path):
    path = tmp_path / "opt_out.json"
    path.write_text(json.dumps({"disabled_cwds": ["/Users/gzur/repos/scratch"]}))
    monkeypatch.setattr(hooks_cli, "_OPT_OUT_PATH", path)
    assert hooks_cli._cwd_opted_out("/Users/gzur/repos/scratch") is True
    # absolute-path normalization
    assert hooks_cli._cwd_opted_out("/Users/gzur/repos/../repos/scratch") is True
    assert hooks_cli._cwd_opted_out("/Users/gzur/repos/other") is False


def test_opt_out_tolerates_malformed_json(monkeypatch, tmp_path):
    path = tmp_path / "opt_out.json"
    path.write_text("{this is not json")
    monkeypatch.setattr(hooks_cli, "_OPT_OUT_PATH", path)
    assert hooks_cli._cwd_opted_out("/anywhere") is False


# ── _cwd_has_mempalace_mcp ───────────────────────────────────────────


def test_cwd_has_mempalace_mcp_true(tmp_path):
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    "/Users/gzur/repos/notes": {
                        "mcpServers": {"mempalace": {"url": "http://127.0.0.1:9876"}}
                    }
                }
            }
        )
    )
    assert (
        hooks_cli._cwd_has_mempalace_mcp("/Users/gzur/repos/notes", config_path=config_path) is True
    )


def test_cwd_has_mempalace_mcp_false_when_project_missing(tmp_path):
    config_path = tmp_path / ".claude.json"
    config_path.write_text(json.dumps({"projects": {}}))
    assert (
        hooks_cli._cwd_has_mempalace_mcp("/Users/gzur/repos/scratch", config_path=config_path)
        is False
    )


def test_cwd_has_mempalace_mcp_false_when_file_missing(tmp_path):
    assert (
        hooks_cli._cwd_has_mempalace_mcp("/anywhere", config_path=tmp_path / "missing.json")
        is False
    )


def test_cwd_has_mempalace_mcp_true_via_global_fallback(tmp_path):
    """Global mcpServers.mempalace counts even with no per-project entry.

    This is the common shape on disk: Claude Code resolves MCP servers
    from the global block; per-project overrides are seeded lazily.
    """
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {"mempalace": {"url": "http://127.0.0.1:9876"}},
                "projects": {"/Users/gzur/repos/mempalace": {"mcpServers": {}}},
            }
        )
    )
    assert (
        hooks_cli._cwd_has_mempalace_mcp("/Users/gzur/repos/mempalace", config_path=config_path)
        is True
    )


def test_cwd_has_mempalace_mcp_false_on_malformed_json(tmp_path):
    config_path = tmp_path / ".claude.json"
    config_path.write_text("{not json")
    assert hooks_cli._cwd_has_mempalace_mcp("/anywhere", config_path=config_path) is False


# ── _format_prompt_context ───────────────────────────────────────────


def test_prompt_context_shows_dated_snippet():
    block = hooks_cli._format_prompt_context(
        [
            {
                "drawer_id": "drawer_notes_journal_abc",
                "source_file": "2026-01-01-entry.md",
                "source_date": "2026-01-01T10:00:00",
                "similarity": 0.48,
                "text": "noise line here\nVICE-1624 fix: one grep exclude for .deploy\nmore",
            }
        ],
        ["VICE-1624"],
    )
    assert block.startswith("[palace: 1 notes may relate")
    assert "2026-01-01 (newest) · 2026-01-01-entry.md · drawer_notes_journal_abc" in block
    assert "one grep exclude for .deploy" in block
    assert "noise line here" not in block
    assert "mempalace_get_drawer" in block


def test_prompt_context_orders_newest_first_and_marks_older():
    block = hooks_cli._format_prompt_context(
        [
            {"drawer_id": "old", "source_date": "2026-09-21T00:00:00", "text": "VICE-1 floor is 7"},
            {"drawer_id": "new", "source_date": "2026-09-24T00:00:00", "text": "VICE-1 floor is 8"},
            {"drawer_id": "nodate", "text": "something else entirely"},
        ],
        ["VICE-1"],
    )
    assert block.index("· new") < block.index("· old") < block.index("· nodate")
    assert "2026-09-24 (newest)" in block
    assert "2026-09-21 (older)" in block
    assert "undated" in block


def test_prompt_context_stays_within_budget():
    candidates = [
        {"drawer_id": f"d{i}", "source_date": f"2026-01-0{i + 1}T00:00:00", "text": "x " * 2000}
        for i in range(5)
    ]
    block = hooks_cli._format_prompt_context(candidates, [])
    assert len(block) <= hooks_cli._CONTEXT_CHAR_BUDGET + 200
    assert block.count("\n- ") >= 3


def test_build_prompt_context_searches_whole_prompt():
    seen = {}

    def fake_search(q):
        seen["q"] = q
        return [{"drawer_id": "a", "similarity": 0.6, "text": "VICE-2007 store note"}]

    brief = (
        "Task: VICE-2007 resolves a cluster's VMS endpoint only through the tenant store. "
        + "Filler sentence that says nothing at all. " * 10
        + "When done, send your result back to @dora via hcom."
    )
    text, info = hooks_cli.build_prompt_context(brief, fake_search)
    assert "VICE-2007" in seen["q"] and "@dora" in seen["q"]
    assert info["method"] == "key_terms+first+last"
    assert "VICE-2007 store note" in text


# ── hook_user_prompt_submit ─────────────────────────────────────────


def test_skips_meta_command(stub_palace_root, cwd_with_mempalace, captured_output):
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "/help me", "cwd": "/Users/gzur/repos/notes"}, "claude-code"
    )
    assert captured_output["payload"] == {}


def test_skips_short_prompt(stub_palace_root, cwd_with_mempalace, captured_output):
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "hi there", "cwd": "/Users/gzur/repos/notes"}, "claude-code"
    )
    assert captured_output["payload"] == {}


def test_skips_when_opted_out(stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch):
    monkeypatch.setattr(hooks_cli, "_cwd_opted_out", lambda _cwd: True)
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what did Alice say about planning", "cwd": "/x"},
        "claude-code",
    )
    assert captured_output["payload"] == {}


def test_skips_when_cwd_has_no_mempalace_mcp(stub_palace_root, captured_output, monkeypatch):
    monkeypatch.setattr(hooks_cli, "_cwd_has_mempalace_mcp", lambda *_a, **_kw: False)
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what did Alice say about planning", "cwd": "/scratch"},
        "claude-code",
    )
    assert captured_output["payload"] == {}


def test_skips_unknown_harness(stub_palace_root, cwd_with_mempalace, captured_output):
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what did Alice say about planning", "cwd": "/x"}, "cursor"
    )
    assert captured_output["payload"] == {}


def test_happy_path_emits_smoke_signal(
    stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch
):
    monkeypatch.setattr(
        hooks_cli,
        "_palace_search",
        lambda *_a, **_kw: [
            {
                "drawer_id": "drawer_notes_journal_4a8b",
                "wing": "notes",
                "room": "journal",
                "source_file": "2026-01-01-entry.md",
                "similarity": 0.48,
            },
            {
                "drawer_id": "drawer_notes_meeting_notes_d642",
                "wing": "notes",
                "room": "meeting_notes",
                "source_file": "2026-01-02.md",
                "similarity": 0.44,
            },
            # Below threshold — must be filtered out.
            {
                "drawer_id": "drawer_low_relevance",
                "wing": "notes",
                "room": "journal",
                "source_file": "noise.md",
                "similarity": 0.30,
            },
        ],
    )

    hooks_cli.hook_user_prompt_submit(
        {
            "prompt": "what did Alice say about planning",
            "cwd": "/Users/gzur/repos/notes",
        },
        "claude-code",
    )

    payload = captured_output["payload"]
    assert payload is not None
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert "drawer_notes_journal_4a8b" in block
    assert "drawer_notes_meeting_notes_d642" in block
    # Sub-threshold candidate must NOT appear.
    assert "drawer_low_relevance" not in block
    assert "2 notes may relate" in block


def test_happy_path_uses_hook_specific_output_envelope(
    stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch
):
    """Regression: Claude Code silently drops a bare top-level
    ``additionalContext``. The injection must be nested under
    ``hookSpecificOutput`` with ``hookEventName == "UserPromptSubmit"``
    or the smoke signal never reaches the main session."""
    monkeypatch.setattr(
        hooks_cli,
        "_palace_search",
        lambda *_a, **_kw: [
            {
                "drawer_id": "drawer_notes_journal_4a8b",
                "wing": "notes",
                "room": "journal",
                "source_file": "2026-01-01-entry.md",
                "similarity": 0.48,
            },
        ],
    )

    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what did Alice say about planning", "cwd": "/Users/gzur/repos/notes"},
        "claude-code",
    )

    payload = captured_output["payload"]
    assert payload is not None
    # No bare top-level key — that shape is silently dropped.
    assert "additionalContext" not in payload
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert "drawer_notes_journal_4a8b" in hook_output["additionalContext"]


def test_empty_match_emits_empty_json(
    stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch
):
    monkeypatch.setattr(hooks_cli, "_palace_search", lambda *_a, **_kw: [])
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "the weather is fine today", "cwd": "/Users/gzur/repos/notes"},
        "claude-code",
    )
    assert captured_output["payload"] == {}


def test_all_below_threshold_emits_empty_json(
    stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch
):
    monkeypatch.setattr(
        hooks_cli,
        "_palace_search",
        lambda *_a, **_kw: [
            {
                "drawer_id": "x",
                "wing": "w",
                "room": "r",
                "source_file": "s.md",
                "similarity": 0.30,
            },
        ],
    )
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what about the weather", "cwd": "/Users/gzur/repos/notes"},
        "claude-code",
    )
    assert captured_output["payload"] == {}


def test_caps_at_max_candidates(stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch):
    """Even if 8 matches survive the threshold, only the top _MAX_CANDIDATES
    appear in the smoke signal — keeps the injection small."""
    monkeypatch.setattr(
        hooks_cli,
        "_palace_search",
        lambda *_a, **_kw: [
            {
                "drawer_id": f"drawer_{i}",
                "wing": "notes",
                "room": "journal",
                "source_file": "f.md",
                "similarity": 0.45,
            }
            for i in range(8)
        ],
    )
    hooks_cli.hook_user_prompt_submit(
        {"prompt": "something about planning", "cwd": "/Users/gzur/repos/notes"},
        "claude-code",
    )
    payload = captured_output["payload"]
    assert payload is not None
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert f"{hooks_cli._MAX_CANDIDATES} notes may relate" in block
    # Beyond the cap should NOT appear.
    assert f"drawer_{hooks_cli._MAX_CANDIDATES}" not in block


def test_palace_search_returns_empty_on_http_error(monkeypatch):
    def _boom(*_a, **_kw):
        raise urllib.error.URLError("daemon down")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert hooks_cli._palace_search("anything", "claude-code/x") == []


def test_palace_search_returns_empty_on_malformed_response(monkeypatch):
    class _FakeResponse:
        def read(self):
            return b"{not valid json"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: _FakeResponse())
    assert hooks_cli._palace_search("anything", "claude-code/x") == []


def test_palace_search_returns_empty_on_timeout(monkeypatch):
    def _boom(*_a, **_kw):
        raise TimeoutError("slow")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert hooks_cli._palace_search("anything", "claude-code/x") == []


def test_full_fail_open_when_daemon_down(
    stub_palace_root, cwd_with_mempalace, captured_output, monkeypatch
):
    """Search blows up → hook emits {} → main session sees no injection,
    no error."""

    def _boom(*_a, **_kw):
        raise urllib.error.URLError("daemon down")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    hooks_cli.hook_user_prompt_submit(
        {"prompt": "what did Alice say about planning", "cwd": "/x"},
        "claude-code",
    )
    assert captured_output["payload"] == {}


def test_run_hook_dispatches_user_prompt_submit(monkeypatch):
    captured: dict[str, tuple | None] = {"call": None}

    def fake_handler(data, harness):
        captured["call"] = (data, harness)

    monkeypatch.setattr(hooks_cli, "hook_user_prompt_submit", fake_handler)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"prompt": "hello world this is long enough"}))
    )

    hooks_cli.run_hook("user-prompt-submit", "claude-code")
    assert captured["call"] is not None
    data, harness = captured["call"]
    assert harness == "claude-code"
    assert data["prompt"] == "hello world this is long enough"
