#!/usr/bin/env python3
"""Periodic mempalace health watcher.

Run via launchd every 5 minutes (see
``scripts/cloud.skyvafnir.mempalace.monitor.plist``). On every tick:

1. Run ``mempalace.health.run_checks``.
2. Diff against last tick's status (state file at
   ``~/.mempalace/monitor_state.json``).
3. On any status transition (ok ↔ warn ↔ fail), fire a macOS Notification
   Center alert via ``osascript``.
4. On any FAIL, dedup-check the beads database and create or update a
   ``--label=mempalace`` bead carrying the check's ``failure_signature``.

Stays quiet when nothing changes. Designed to be cheap enough to run on a
5-minute cadence: every check is read-only and avoids opening a chromadb
persistent client.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


STATE_DIR = Path.home() / ".mempalace"
STATE_FILE = STATE_DIR / "monitor_state.json"
LOG_FILE = STATE_DIR / "logs" / "monitor.log"


def _log(line: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a") as f:
        f.write(f"{ts} {line}\n")


def _load_state() -> dict[str, str]:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _notify(title: str, body: str) -> None:
    """macOS Notification Center via osascript. Best-effort; never raises."""
    osascript = shutil.which("osascript")
    if not osascript:
        _log(f"osascript missing; would have notified: {title} — {body}")
        return
    # Escape double quotes for AppleScript string literals.
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        subprocess.run(
            [osascript, "-e", script],
            timeout=5.0,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        _log(f"notify failed: {e}")


def _file_bead(check) -> None:
    """Dedup against open beads, create or update on failure_signature."""
    if not check.signature:
        return
    bd = shutil.which("bd")
    if not bd:
        _log("bd not on PATH; skipping bead file")
        return
    try:
        listing = subprocess.run(
            [bd, "list", "--label=mempalace", "--status=open"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        _log(f"bd list failed: {e}")
        return

    # Beads `list` prints one issue per line with the title in plain text;
    # match by failure_signature substring.
    if check.signature in listing.stdout:
        _log(f"bead with signature already open: {check.signature}")
        return

    title = f"monitor: {check.name} {check.status} — {check.signature}"[:140]
    description = (
        f"Detected by ``scripts/mempalace_monitor.py``.\n\n"
        f"failure_signature: {check.signature}\n"
        f"check: {check.name}\n"
        f"status: {check.status}\n"
        f"detail: {check.detail}\n"
        f"value: {check.value}\n"
        f"extra: {json.dumps(check.extra)}\n"
    )
    try:
        out = subprocess.run(
            [
                bd,
                "create",
                "--type=bug",
                "--label=mempalace",
                "--priority=2",
                f"--title={title}",
                f"--description={description}",
            ],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        _log(f"bd create failed: {e}")
        return
    _log(f"bead created: {check.signature}")
    if out.returncode != 0:
        _log(f"bd create rc={out.returncode} stderr={out.stderr.strip()[:200]}")


def main() -> int:
    # Add the repo to sys.path so the script works regardless of how launchd
    # spawns it (the .mempalace-env python is the canonical interpreter).
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from mempalace.health import FAIL, OK, WARN, overall_status, run_checks
    except ImportError as e:
        _log(f"import mempalace.health failed: {e}")
        return 2

    checks = run_checks()
    prior = _load_state()
    current = {c.name: c.status for c in checks}
    overall = overall_status(checks)

    transitions = []
    for c in checks:
        prev = prior.get(c.name)
        if prev != c.status:
            transitions.append((c, prev))

    if transitions:
        for c, prev in transitions:
            _log(
                f"transition {c.name}: {prev or 'unknown'} -> {c.status} ({c.detail})"
            )
            if c.status == FAIL:
                _notify(
                    f"MemPalace: {c.name} FAIL",
                    c.detail,
                )
                _file_bead(c)
            elif c.status == WARN:
                _notify(
                    f"MemPalace: {c.name} warn",
                    c.detail,
                )
            elif c.status == OK and prev in (WARN, FAIL):
                _notify(
                    f"MemPalace: {c.name} recovered",
                    c.detail,
                )
    else:
        _log(f"tick clean; overall={overall}")

    _save_state(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
