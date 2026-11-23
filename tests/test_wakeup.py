"""Tests for the task-aware wake-up (mempalace.wakeup) and its hook path."""

from __future__ import annotations

import subprocess

import pytest

from mempalace import hooks_cli, wakeup


def _hit(drawer_id, text, date, sim=0.6):
    return {
        "drawer_id": drawer_id,
        "text": text,
        "source_date": date,
        "source_file": f"{drawer_id}.jsonl",
        "similarity": sim,
    }


# ── signals ───────────────────────────────────────────────────────────


def test_branch_terms_reads_ticket_key_and_words():
    keys, words = wakeup.branch_terms("bugfix/vice-1957-revert-staging-gar")
    assert keys == ["VICE-1957"]
    assert words == ["revert", "staging", "gar"]


def test_branch_terms_reads_mr_number_and_skips_main():
    assert wakeup.branch_terms("review/mr-1648")[0] == ["!1648"]
    assert wakeup.branch_terms("main") == ([], [])
    assert wakeup.branch_terms("") == ([], [])


def test_task_signals_reads_git_branch_and_prompt_keys(tmp_path):
    repo = tmp_path / "polaris"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feat/vice-2192-nightly", str(repo)], check=True)
    sig = wakeup.task_signals(str(repo), prompt="Look at !1664 again. Then report to @heno.")
    assert sig["project"] == "polaris"
    assert sig["branch"] == "feat/vice-2192-nightly"
    assert sig["keys"][:3] == ["VICE-2192", "!1664", "@heno"]
    assert sig["first_sentence"] == "Look at !1664 again."


def test_task_signals_off_git_uses_cwd_name(tmp_path):
    sig = wakeup.task_signals(str(tmp_path / "notes"), prompt="")
    assert sig["project"] == "notes"
    assert sig["branch"] == ""
    assert sig["keys"] == []


def test_task_queries_one_per_key_then_general():
    sig = {
        "project": "polaris",
        "branch_words": ["nightly"],
        "keys": ["VICE-1", "VICE-2", "VICE-3", "VICE-4"],
        "first_sentence": "Fix the job.",
    }
    assert wakeup.task_queries(sig) == [
        "VICE-1",
        "VICE-2",
        "VICE-3",
        "polaris nightly Fix the job.",
    ]


# ── build_task_wakeup ─────────────────────────────────────────────────


def test_wakeup_dedupes_and_shows_newest_first_with_facts_header():
    sig = {
        "project": "polaris",
        "branch": "feat/vice-2192",
        "branch_words": [],
        "keys": ["VICE-2192"],
    }
    results = {
        "VICE-2192": [
            _hit("old", "VICE-2192 brief: allow_failure stays true for now", "2026-10-20T10:00:00"),
            _hit(
                "new",
                "VICE-2192 merged as c917a74bf, the nightly job blocks",
                "2026-10-23T10:00:00",
            ),
        ],
        "polaris": [
            _hit(
                "new",
                "VICE-2192 merged as c917a74bf, the nightly job blocks",
                "2026-10-23T10:00:00",
            ),
            _hit(
                "copy",
                "VICE-2192 merged as c917a74bf, the nightly job blocks",
                "2026-10-24T10:00:00",
            ),
        ],
    }
    text, info = wakeup.build_task_wakeup(sig, lambda q: results.get(q, []))
    assert text.startswith(
        "[palace wake-up · project polaris · branch feat/vice-2192 · keys VICE-2192: 2 "
    )
    assert text.index("new") < text.index("old")
    assert "(newest)" in text and "(older)" in text
    assert "copy" not in text  # same text as "new"
    assert info["drawer_ids"] == ["new", "old"]
    assert info["queries"] == ["VICE-2192", "polaris"]


def test_wakeup_prefers_key_drawers_over_newer_noise():
    sig = {"project": "p", "branch": "", "branch_words": [], "keys": ["VICE-7"]}
    noise = [
        _hit(f"n{i}", f"unrelated note {i} about lunch", f"2026-11-1{i}T00:00:00") for i in range(9)
    ]
    key = [_hit("k", "VICE-7 decision: remove the fallback", "2026-01-01T00:00:00", sim=0.1)]
    text, info = wakeup.build_task_wakeup(sig, lambda q: key if q == "VICE-7" else noise)
    assert "k" in info["drawer_ids"]


def test_wakeup_before_drops_later_and_undated_drawers():
    sig = {"project": "p", "branch": "", "branch_words": [], "keys": ["VICE-7"]}
    hits = [
        _hit("a", "VICE-7 early note", "2026-10-01T00:00:00"),
        _hit("b", "VICE-7 later note", "2026-10-09T00:00:00"),
        {"drawer_id": "c", "text": "VICE-7 undated", "similarity": 0.9},
    ]
    text, info = wakeup.build_task_wakeup(sig, lambda q: hits, before="2026-10-05T00:00:00")
    assert info["drawer_ids"] == ["a"]


def test_wakeup_skips_weak_drawers_without_a_key():
    sig = {"project": "p", "branch": "", "branch_words": [], "keys": []}
    hits = [_hit("weak", "something else", "2026-10-01T00:00:00", sim=0.1)]
    text, info = wakeup.build_task_wakeup(sig, lambda q: hits)
    assert text == ""
    assert info["drawer_ids"] == []


def test_wakeup_stays_within_budget():
    sig = {"project": "p", "branch": "", "branch_words": [], "keys": ["VICE-7"]}
    hits = [
        _hit(f"d{i}", f"VICE-7 {i} " + "x " * 600, f"2026-10-0{i + 1}T00:00:00") for i in range(7)
    ]
    text, _ = wakeup.build_task_wakeup(sig, lambda q: hits)
    assert len(text) <= hooks_cli._CONTEXT_CHAR_BUDGET + 200


# ── hook: first prompt only ───────────────────────────────────────────


@pytest.fixture
def hook_env(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks_cli, "_palace_root_exists", lambda: True)
    monkeypatch.setattr(hooks_cli, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks_cli, "_cwd_has_mempalace_mcp", lambda *_a, **_kw: True)
    monkeypatch.setattr(hooks_cli, "_cwd_opted_out", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        wakeup, "git_context", lambda cwd: {"project": "polaris", "branch": "fix/vice-7"}
    )
    out = {}
    monkeypatch.setattr(hooks_cli, "_output", lambda data: out.update(payload=data))
    hits = [_hit("w1", "VICE-7 decision: remove the fallback", "2026-10-01T00:00:00")]
    monkeypatch.setattr(hooks_cli, "_palace_search", lambda q, tag: hits)
    return out


def _prompt(session="s1"):
    return {
        "prompt": "please continue the work on this",
        "cwd": "/tmp/polaris",
        "session_id": session,
    }


def test_hook_adds_wakeup_once_per_session(hook_env, monkeypatch):
    monkeypatch.setenv("MEMPALACE_TASK_WAKEUP", "1")
    hooks_cli.hook_user_prompt_submit(_prompt(), "claude-code")
    first = hook_env["payload"]["hookSpecificOutput"]["additionalContext"]
    assert first.startswith("[palace wake-up · project polaris · branch fix/vice-7")
    # the prompt block leaves out the drawer the wake-up showed
    assert first.count("· w1\n") == 1

    hooks_cli.hook_user_prompt_submit(_prompt(), "claude-code")
    assert hook_env["payload"]["hookSpecificOutput"]["additionalContext"].startswith("[palace: 1")

    hooks_cli.hook_user_prompt_submit(_prompt("s2"), "claude-code")
    assert "palace wake-up" in hook_env["payload"]["hookSpecificOutput"]["additionalContext"]


def test_hook_wakeup_is_off_by_default(hook_env, monkeypatch):
    monkeypatch.delenv("MEMPALACE_TASK_WAKEUP", raising=False)
    hooks_cli.hook_user_prompt_submit(_prompt(), "claude-code")
    assert "palace wake-up" not in hook_env["payload"]["hookSpecificOutput"]["additionalContext"]
