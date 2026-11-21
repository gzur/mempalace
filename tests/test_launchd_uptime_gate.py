"""The launchd check must age its crash counter out against the live process.

launchd does not reset ``successive crashes`` when a job stabilises. Before
this gate existed, the 2026-09-17 segfault loop left crashes=35 pinned and the
check reported FAIL every 5 minutes for 57 days while the daemon ran clean --
which is the same as reporting nothing at all.
"""

import subprocess

import pytest

from mempalace import health


LAUNCHCTL_OUT = """\
cloud.skyvafnir.mempalace = {{
\tstate = running
\tpid = {pid}
\tsuccessive crashes = {crashes}
\tlast terminating signal = {signal}
}}
"""


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_run(*, crashes, signal, etime, pid=4242, ps_rc=0):
    """Stub subprocess.run for both the launchctl probe and the ps probe."""

    def run(cmd, **kwargs):
        if cmd[0] == "launchctl":
            return _Result(LAUNCHCTL_OUT.format(pid=pid, crashes=crashes, signal=signal))
        if cmd[0] == "ps":
            if etime is None:
                raise FileNotFoundError("ps")
            return _Result(etime + "\n", ps_rc)
        raise AssertionError(f"unexpected command {cmd!r}")

    return run


# --- etime parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("57-15:20:21", 57 * 86400 + 15 * 3600 + 20 * 60 + 21),
        ("1-00:00:00", 86400),
        ("02:03:04", 2 * 3600 + 3 * 60 + 4),
        ("05:30", 330),
        ("00:09", 9),
    ],
)
def test_etime_parsing(monkeypatch, raw, expected):
    monkeypatch.setattr(subprocess, "run", _fake_run(crashes=0, signal="", etime=raw))
    assert health._process_uptime_seconds(4242) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "garbage", "x-01:02:03", "1:2:3:4"])
def test_etime_unparseable_is_none(monkeypatch, raw):
    monkeypatch.setattr(subprocess, "run", _fake_run(crashes=0, signal="", etime=raw))
    assert health._process_uptime_seconds(4242) is None


def test_no_pid_is_none():
    assert health._process_uptime_seconds(None) is None


# --- the gate ---------------------------------------------------------------


def test_stale_counter_on_a_long_lived_process_reads_ok(monkeypatch):
    """The regression this file exists for: 35 crashes, 57 days of uptime."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(crashes=35, signal="Segmentation fault: 11", etime="57-15:20:21"),
    )
    c = health.check_launchd_status()
    assert c.status == health.OK
    assert c.extra["historical"] is True
    # The history must survive in the detail, not be swallowed.
    assert "35 crashes" in c.detail
    assert "Segmentation fault: 11" in c.detail


def test_a_real_flap_still_fails(monkeypatch):
    """Same counter, but the process respawned 40 seconds ago."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(crashes=35, signal="Segmentation fault: 11", etime="00:40"),
    )
    c = health.check_launchd_status()
    assert c.status == health.FAIL
    assert c.signature == "mempalace launchd flapping crashes=35"
    assert "historical" not in c.extra


def test_unknown_uptime_keeps_alerting(monkeypatch):
    """ps unavailable must not silence the check."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(crashes=35, signal="Segmentation fault: 11", etime=None),
    )
    c = health.check_launchd_status()
    assert c.status == health.FAIL


def test_signal_without_crashes_also_ages_out(monkeypatch):
    """The 'clean now but last signal was X' WARN was stuck for the same reason."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(crashes=0, signal="Segmentation fault: 11", etime="3-00:00:00"),
    )
    c = health.check_launchd_status()
    assert c.status == health.OK
    assert "Segmentation fault: 11" in c.detail


def test_boundary_is_not_crossed_early(monkeypatch):
    """One second under the threshold is still a live flap."""
    monkeypatch.setattr(
        subprocess, "run", _fake_run(crashes=35, signal="", etime="59:59")
    )
    assert health.check_launchd_status().status == health.FAIL
    monkeypatch.setattr(
        subprocess, "run", _fake_run(crashes=35, signal="", etime="01:00:00")
    )
    assert health.check_launchd_status().status == health.OK


def test_clean_job_is_untouched(monkeypatch):
    """No crashes, no signal: the gate must not rewrite the normal OK path."""
    monkeypatch.setattr(
        subprocess, "run", _fake_run(crashes=0, signal="", etime="10-00:00:00")
    )
    c = health.check_launchd_status()
    assert c.status == health.OK
    assert c.detail == "pid=4242, runs clean"
