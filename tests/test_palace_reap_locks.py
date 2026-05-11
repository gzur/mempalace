"""Tests for palace.reap_stale_locks()."""

import os
import threading
import time

import pytest

from mempalace.palace import mine_lock, reap_stale_locks


@pytest.fixture
def lock_dir(tmp_path):
    d = tmp_path / "locks"
    d.mkdir()
    return str(d)


def _touch(path: str, age_seconds: float = 0) -> None:
    with open(path, "w"):
        pass
    if age_seconds:
        past = time.time() - age_seconds
        os.utime(path, (past, past))


def test_reap_no_dir_returns_zero(tmp_path):
    assert reap_stale_locks(str(tmp_path / "missing")) == 0


def test_reap_empty_dir(lock_dir):
    assert reap_stale_locks(lock_dir) == 0


def test_reap_unheld_lockfile(lock_dir):
    path = os.path.join(lock_dir, "abc.lock")
    _touch(path)
    assert reap_stale_locks(lock_dir) == 1
    assert not os.path.exists(path)


def test_reap_skips_non_lock_files(lock_dir):
    keep = os.path.join(lock_dir, "not_a_lock.txt")
    _touch(keep)
    target = os.path.join(lock_dir, "x.lock")
    _touch(target)
    assert reap_stale_locks(lock_dir) == 1
    assert os.path.exists(keep)
    assert not os.path.exists(target)


def test_reap_skips_currently_held_lock(lock_dir):
    """Held lock survives reap; reap returns 0 for that file."""
    if os.name == "nt":
        pytest.skip("flock probe is POSIX-only")
    import fcntl

    path = os.path.join(lock_dir, "held.lock")
    _touch(path)
    fh = open(path, "r+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        assert reap_stale_locks(lock_dir) == 0
        assert os.path.exists(path)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def test_ttl_backstop_removes_old_held_lock(lock_dir):
    """TTL fires before flock probe, removes even if mtime > stale_age."""
    path = os.path.join(lock_dir, "ancient.lock")
    _touch(path, age_seconds=48 * 3600)  # 48h old
    assert reap_stale_locks(lock_dir, stale_age=24 * 3600) == 1
    assert not os.path.exists(path)


def test_reap_mixed_held_and_orphan(lock_dir):
    if os.name == "nt":
        pytest.skip("flock probe is POSIX-only")
    import fcntl

    orphan = os.path.join(lock_dir, "orphan.lock")
    held = os.path.join(lock_dir, "held.lock")
    ancient = os.path.join(lock_dir, "ancient.lock")
    _touch(orphan)
    _touch(held)
    _touch(ancient, age_seconds=48 * 3600)

    fh = open(held, "r+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        removed = reap_stale_locks(lock_dir, stale_age=24 * 3600)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()

    assert removed == 2  # orphan + ancient
    assert not os.path.exists(orphan)
    assert not os.path.exists(ancient)
    assert os.path.exists(held)


def test_mine_lock_still_serializes_after_reaper_change(monkeypatch, tmp_path):
    """mine_lock must still mutually exclude — reaper additions must not break it."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setattr("mempalace.palace._LOCK_DIR", str(lock_dir))

    enter_times = []
    exit_times = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        with mine_lock("/fake/path"):
            enter_times.append(time.time())
            time.sleep(0.1)
            exit_times.append(time.time())

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    enter_times.sort()
    exit_times.sort()
    # Second enter must be after first exit
    assert enter_times[1] >= exit_times[0] - 0.01
