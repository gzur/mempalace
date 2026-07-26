"""Tests for ``scripts/mempalace_monitor.py``.

The script is loaded by file path because the ``scripts/`` directory is
not on the package path. We exercise ``_maintenance_status`` and the
suppression branch of ``main`` by re-pointing module-level paths at a
pytest ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    """Import scripts/mempalace_monitor.py as a module and re-point its
    state paths at tmp_path so each test is hermetic."""
    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_mempalace_monitor_test_module",
        repo_root / "scripts" / "mempalace_monitor.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(mod, "STATE_FILE", state_dir / "monitor_state.json")
    monkeypatch.setattr(mod, "LOG_FILE", state_dir / "logs" / "monitor.log")
    monkeypatch.setattr(mod, "MAINTENANCE_FLAG", state_dir / "MAINTENANCE")
    return mod


class TestMaintenanceStatus:
    def test_no_flag_means_not_suppressed(self, monitor):
        suppressed, age = monitor._maintenance_status()
        assert suppressed is False
        assert age is None

    def test_recent_flag_suppresses(self, monitor):
        monitor.MAINTENANCE_FLAG.write_text("")
        suppressed, age = monitor._maintenance_status()
        assert suppressed is True
        assert age is not None
        assert age < 1.0  # just created

    def test_stale_flag_reports_age(self, monitor):
        monitor.MAINTENANCE_FLAG.write_text("")
        # Backdate the flag by 8 hours.
        old = time.time() - 8 * 3600
        import os

        os.utime(monitor.MAINTENANCE_FLAG, (old, old))
        suppressed, age = monitor._maintenance_status()
        assert suppressed is True
        assert age is not None and age >= 7.9


class TestMainSuppression:
    def test_main_returns_zero_without_running_checks_when_flag_set(self, monitor, monkeypatch):
        # ``mempalace.health`` would be the heavy import; if main runs
        # checks even though the flag is set, this will blow up.
        def _explode(*_a, **_kw):
            raise AssertionError("run_checks must not be called under maintenance")

        monkeypatch.setitem(
            __import__("sys").modules,
            "mempalace.health",
            type(
                "_FakeHealth",
                (),
                {
                    "FAIL": "fail",
                    "WARN": "warn",
                    "OK": "ok",
                    "overall_status": _explode,
                    "run_checks": _explode,
                },
            ),
        )

        monitor.MAINTENANCE_FLAG.write_text("")
        notifies: list[tuple[str, str]] = []
        monkeypatch.setattr(monitor, "_notify", lambda t, b: notifies.append((t, b)))
        assert monitor.main() == 0
        assert notifies == []  # recent flag — no warn

    def test_main_warns_once_on_stale_flag(self, monitor, monkeypatch):
        monitor.MAINTENANCE_FLAG.write_text("")
        import os

        old = time.time() - (monitor.STALE_FLAG_HOURS + 1) * 3600
        os.utime(monitor.MAINTENANCE_FLAG, (old, old))

        notifies: list[tuple[str, str]] = []
        monkeypatch.setattr(monitor, "_notify", lambda t, b: notifies.append((t, b)))

        assert monitor.main() == 0
        assert len(notifies) == 1
        assert "stale" in notifies[0][0].lower()

        # Second tick with the same stale flag must NOT re-warn.
        assert monitor.main() == 0
        assert len(notifies) == 1


class _Check:
    """Minimal stand-in for ``mempalace.health.CheckResult``."""

    def __init__(self, name, status, detail="detail", signature="sig"):
        self.name = name
        self.status = status
        self.detail = detail
        self.signature = signature
        self.value = None
        self.extra: dict = {}


def _install_fake_health(monkeypatch, statuses):
    """Install a fake ``mempalace.health`` whose ``run_checks`` yields one
    single-check tick per configured status string, in order."""
    import sys

    seq = iter(statuses)

    def run_checks():
        return [_Check("daemon_port", next(seq))]

    def overall_status(checks):
        return checks[0].status

    fake = type(
        "_FakeHealth",
        (),
        {
            "FAIL": "fail",
            "WARN": "warn",
            "OK": "ok",
            "run_checks": staticmethod(run_checks),
            "overall_status": staticmethod(overall_status),
        },
    )
    monkeypatch.setitem(sys.modules, "mempalace.health", fake)


class TestDebounce:
    def test_pages_only_on_third_consecutive_fail_then_recovers(self, monitor, monkeypatch):
        assert monitor.DEBOUNCE_TICKS == 3
        notifies: list[tuple[str, str]] = []
        beads: list = []
        monkeypatch.setattr(monitor, "_notify", lambda t, b: notifies.append((t, b)))
        monkeypatch.setattr(monitor, "_file_bead", lambda c: beads.append(c))
        _install_fake_health(monkeypatch, ["fail", "fail", "fail", "fail", "ok"])

        monitor.main()  # fail 1/3 — held
        assert notifies == [] and beads == []
        monitor.main()  # fail 2/3 — held
        assert notifies == [] and beads == []

        monitor.main()  # fail 3/3 — page once
        assert len(notifies) == 1 and "FAIL" in notifies[0][0]
        assert len(beads) == 1

        monitor.main()  # fail 4 — already announced, stay quiet
        assert len(notifies) == 1 and len(beads) == 1

        monitor.main()  # ok — recovery announced once
        assert len(notifies) == 2 and "recovered" in notifies[1][0].lower()

    def test_single_tick_blip_is_never_paged(self, monitor, monkeypatch):
        """A lone FAIL that clears next tick (the sleep/wake case) is silent —
        no FAIL page and no spurious 'recovered'."""
        notifies: list[tuple[str, str]] = []
        beads: list = []
        monkeypatch.setattr(monitor, "_notify", lambda t, b: notifies.append((t, b)))
        monkeypatch.setattr(monitor, "_file_bead", lambda c: beads.append(c))
        _install_fake_health(monkeypatch, ["ok", "fail", "ok"])

        monitor.main()
        monitor.main()
        monitor.main()
        assert notifies == []
        assert beads == []
