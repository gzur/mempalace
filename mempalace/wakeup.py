"""Task-aware wake-up: recent drawers for the work a session starts on.

The L0 + L1 wake-up (:mod:`mempalace.layers`) is the same text for every
session. This module builds a short block from what the session is
about instead: the project (git top level or cwd), the git branch, the
ticket keys and MR numbers in the branch name and the first prompt, and
the first sentence of that prompt. It searches for each signal, keeps
the relevant drawers, drops duplicates, and renders them dated, newest
first, inside the same budget as the prompt hook (about 600 tokens).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional

_BRANCH_KEY = re.compile(r"(?<![a-z0-9])([a-z][a-z0-9]{1,9})-(\d{1,6})(?![0-9])", re.I)
_BRANCH_MR = re.compile(r"(?:^|[/_-])(?:mr|merge)[-_]?(\d{2,6})(?![0-9])", re.I)
_BRANCH_SKIP = {"main", "master", "develop", "dev", "trunk", "head"}
_BRANCH_NOISE = {"feature", "feat", "fix", "bugfix", "hotfix", "chore", "ci", "docs", "trial"}
_MAX_KEY_QUERIES = 3
_DEDUPE_CHARS = 160
_WS = re.compile(r"\s+")


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def git_context(cwd: str) -> dict:
    """Return ``{project, branch}`` for ``cwd``; empty strings off git."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    # symbolic-ref also works before the first commit; empty when detached.
    branch = _git(cwd, "symbolic-ref", "--short", "-q", "HEAD")
    project = os.path.basename(top or os.path.abspath(cwd).rstrip(os.sep))
    return {"project": project, "branch": branch}


def branch_terms(branch: str) -> tuple:
    """Return ``(keys, words)`` from a branch name.

    ``bugfix/vice-1957-revert-staging-gar`` gives ``(["VICE-1957"],
    ["revert", "staging", "gar"])``. Branches such as ``main`` give
    nothing.
    """
    if not branch or branch.lower() in _BRANCH_SKIP:
        return [], []
    keys = [
        f"{p.upper()}-{n}"
        for p, n in _BRANCH_KEY.findall(branch)
        if p.lower() not in ("mr", "merge")
    ]
    keys += [f"!{n}" for n in _BRANCH_MR.findall(branch)]
    rest = _BRANCH_KEY.sub(" ", branch)
    words = [w for w in re.split(r"[/_\-.\s]+", rest) if w]
    words = [w for w in words if w.lower() not in _BRANCH_NOISE and not w.isdigit()]
    return _unique(keys), words


def _unique(items) -> list:
    seen, out = set(), []
    for item in items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            out.append(item)
    return out


def task_signals(cwd: str, branch: Optional[str] = None, prompt: str = "") -> dict:
    """Collect the task signals for a session.

    ``branch=None`` reads it from git in ``cwd``. Returns ``project``,
    ``branch``, ``keys`` (ticket keys / MR numbers / other literal key
    terms, branch first) and ``first_sentence`` of the prompt.
    """
    from .query_sanitizer import _meaningful_sentences, extract_key_terms

    ctx = git_context(cwd) if cwd else {"project": "", "branch": ""}
    if branch is not None:
        ctx["branch"] = branch
    keys, words = branch_terms(ctx["branch"])
    prompt = prompt or ""
    keys = _unique(keys + extract_key_terms(prompt))
    sentences = _meaningful_sentences(prompt)
    return {
        "project": ctx["project"],
        "branch": ctx["branch"],
        "branch_words": words,
        "keys": keys,
        "first_sentence": sentences[0][:160] if sentences else "",
    }


def task_queries(signals: dict) -> list:
    """Return the search queries for ``signals``: one per key, then one general."""
    queries = []
    project = signals.get("project", "")
    for key in signals.get("keys", [])[:_MAX_KEY_QUERIES]:
        queries.append(key)
    general = " ".join(
        part
        for part in (
            project,
            " ".join(signals.get("branch_words", [])),
            signals.get("first_sentence", ""),
        )
        if part
    ).strip()
    if general and general not in queries:
        queries.append(general)
    return queries


def _dedupe_key(result: dict) -> str:
    return _WS.sub(" ", (result.get("text") or "")).strip().lower()[:_DEDUPE_CHARS]


def build_task_wakeup(
    signals: dict,
    search_fn: Callable[[str], list],
    before: Optional[str] = None,
) -> tuple:
    """Return ``(text, info)``: the wake-up block for ``signals``.

    ``search_fn(query) -> list[dict]`` is the daemon call in a hook and
    an in-process search in the CLI. ``before`` (ISO time) drops drawers
    dated at or after it, so a test can see only what existed when a task
    started. ``info`` holds the queries, the drawer IDs shown and the
    token estimate.
    """
    from .hooks_cli import _MAX_CANDIDATES, _format_prompt_context, _is_candidate

    queries = task_queries(signals)
    keys = [k.lower() for k in signals.get("keys", [])]
    picked: list = []
    seen_ids: set = set()
    seen_text: set = set()
    for query in queries:
        for r in search_fn(query) or []:
            if not isinstance(r, dict):
                continue
            text = (r.get("text") or "").lower()
            has_key = any(k in text for k in keys)
            if not (has_key or _is_candidate(r)):
                continue
            if before and (not r.get("source_date") or r["source_date"] >= before):
                continue
            rid = r.get("drawer_id") or id(r)
            dkey = _dedupe_key(r)
            if rid in seen_ids or dkey in seen_text:
                continue
            seen_ids.add(rid)
            seen_text.add(dkey)
            picked.append(r)
    info = {"queries": queries, "signals": signals, "candidates": len(picked)}
    if not picked:
        info.update(drawer_ids=[], approx_tokens=0)
        return "", info
    # Pick by relevance (drawers that name a task key first, in query
    # order), then show the picked ones newest first.
    picked.sort(key=lambda r: not any(k in (r.get("text") or "").lower() for k in keys))
    picked = picked[: _MAX_CANDIDATES + 2]
    picked.sort(key=lambda r: r.get("source_date") or "", reverse=True)
    facts = [f"project {signals['project']}"] if signals.get("project") else []
    if signals.get("branch"):
        facts.append(f"branch {signals['branch']}")
    if signals.get("keys"):
        facts.append("keys " + ", ".join(signals["keys"][:_MAX_KEY_QUERIES]))
    header = "[palace wake-up"
    if facts:
        header += " · " + " · ".join(facts)
    header += (
        ": {n} recent notes for this task, newest first. A newer note can replace an older one."
    )
    text = _format_prompt_context(picked, signals.get("keys", []), header=header)
    info["drawer_ids"] = [
        r.get("drawer_id") for r in picked if r.get("drawer_id") and r["drawer_id"] in text
    ]
    info["approx_tokens"] = len(text) // 4
    return text, info
