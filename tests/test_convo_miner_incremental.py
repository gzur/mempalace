"""Incremental convo mine: a grown transcript files only its new chunks."""

import json
import os
import re
from pathlib import Path

import chromadb
import pytest

from mempalace.convo_miner import (
    CONVO_STATE_FILE,
    _file_signature,
    _mine_plan,
    mine_convos,
)


def _records(start, n):
    """n Claude Code exchanges (user + assistant records), dated in order."""
    out = []
    for k in range(start, start + n):
        ts = f"2026-11-27T10:{k:02d}:00.000Z"
        out.append(
            {
                "type": "user",
                "timestamp": ts,
                "message": {"role": "user", "content": f"Question {k}: how does part {k} work?"},
            }
        )
        out.append(
            {
                "type": "assistant",
                "timestamp": ts,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Answer {k}: part {k} reads the records in order.",
                        }
                    ],
                },
            }
        )
    return "".join(json.dumps(r) + "\n" for r in out)


def _drawers(palace, source):
    col = chromadb.PersistentClient(path=str(palace)).get_collection("mempalace_drawers")
    got = col.get(where={"source_file": str(source)}, include=["documents", "metadatas"])
    return {
        i: (d, m)
        for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])
        if m.get("ingest_mode") != "registry"
    }


def _filed(out):
    return int(re.search(r"Drawers filed: (\d+)", out).group(1))


def _skipped(out):
    return int(re.search(r"Files skipped \(already filed\): (\d+)", out).group(1))


@pytest.fixture
def convo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "session.jsonl"
    f.write_text(_records(0, 3))
    return src, f.resolve(), tmp_path / "palace"


def _mine(src, palace, capsys):
    capsys.readouterr()
    mine_convos(str(src), str(palace), wing="t")
    # The first mine of a process imports mcp_server, which points
    # sys.stdout at stderr: read both streams.
    got = capsys.readouterr()
    return got.out + got.err


def test_grown_file_adds_only_new_drawers(convo, capsys):
    src, f, palace = convo
    first = _filed(_mine(src, palace, capsys))
    before = _drawers(palace, f)
    assert first == len(before) == 3

    with open(f, "a") as fh:
        fh.write(_records(3, 2))
    out = _mine(src, palace, capsys)
    after = _drawers(palace, f)

    assert _filed(out) == 2
    assert len(after) == 5
    # The old drawers keep their ids and their verbatim text.
    for did, (doc, _meta) in before.items():
        assert after[did][0] == doc

    # The result equals a fresh mine of the grown file.
    fresh_palace = palace.parent / "fresh"
    mine_convos(str(src), str(fresh_palace), wing="t")
    fresh = _drawers(fresh_palace, f)
    assert sorted(d for d, _m in after.values()) == sorted(d for d, _m in fresh.values())
    assert set(after) == set(fresh)


def test_second_run_without_change_adds_zero(convo, capsys):
    src, f, palace = convo
    _mine(src, palace, capsys)
    out = _mine(src, palace, capsys)
    assert _filed(out) == 0
    assert _skipped(out) == 1


def test_mtime_only_touch_is_skipped(convo, capsys):
    src, f, palace = convo
    _mine(src, palace, capsys)
    os.utime(f, (1, 1))
    out = _mine(src, palace, capsys)
    assert _filed(out) == 0
    assert _skipped(out) == 1


def test_rewritten_file_is_mined_whole(convo, capsys):
    src, f, palace = convo
    _mine(src, palace, capsys)
    # A new inode with other content (an editor save, a copy over the file).
    tmp = f.with_suffix(".tmp")
    tmp.write_text(_records(10, 4))
    os.replace(tmp, f)
    out = _mine(src, palace, capsys)
    docs = [d for d, _m in _drawers(palace, f).values()]
    assert _filed(out) == 4
    assert len(docs) == 4
    assert not any("Question 0:" in d for d in docs)


def test_truncated_file_is_mined_whole(convo, capsys):
    src, f, palace = convo
    with open(f, "a") as fh:
        fh.write(_records(3, 3))
    _mine(src, palace, capsys)
    assert len(_drawers(palace, f)) == 6
    # Same inode, fewer bytes.
    with open(f, "r+") as fh:
        fh.truncate(0)
        fh.write(_records(0, 2))
    out = _mine(src, palace, capsys)
    assert _filed(out) == 2
    assert len(_drawers(palace, f)) == 2


def test_same_size_other_bytes_is_mined_whole(convo, capsys):
    src, f, palace = convo
    _mine(src, palace, capsys)
    raw = f.read_text()
    with open(f, "r+") as fh:
        fh.write(raw.replace("Question 0", "Question X"))
    out = _mine(src, palace, capsys)
    docs = [d for d, _m in _drawers(palace, f).values()]
    assert _filed(out) == 3
    assert any("Question X" in d for d in docs)
    assert not any("Question 0:" in d for d in docs)


def test_palace_without_state_files_only_what_it_lacks(convo, capsys):
    """A palace mined before the state file existed: the chunk diff fills it."""
    src, f, palace = convo
    _mine(src, palace, capsys)
    os.remove(palace / CONVO_STATE_FILE)
    with open(f, "a") as fh:
        fh.write(_records(3, 1))
    out = _mine(src, palace, capsys)
    assert _filed(out) == 1
    assert len(_drawers(palace, f)) == 4
    # The state is back: the next run skips.
    assert _skipped(_mine(src, palace, capsys)) == 1


def test_grown_last_exchange_refreshes_its_drawer(convo, capsys):
    """Records appended to the last exchange change its drawer in place."""
    src, f, palace = convo
    _mine(src, palace, capsys)
    extra = {
        "type": "assistant",
        "timestamp": "2026-11-27T10:02:30.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Also a tail."}]},
    }
    with open(f, "a") as fh:
        fh.write(json.dumps(extra) + "\n")
    out = _mine(src, palace, capsys)
    docs = [d for d, _m in _drawers(palace, f).values()]
    assert _filed(out) == 1
    assert len(docs) == 3
    assert any("Also a tail." in d for d in docs)


def test_empty_then_grown_file_drops_its_sentinel(tmp_path, capsys):
    """A file too short at its first mine gets a sentinel; real drawers replace it."""
    src = tmp_path / "src"
    src.mkdir()
    f = (src / "session.txt").resolve()
    f.write_text("hi\n")
    palace = tmp_path / "palace"
    _mine(src, palace, capsys)
    col = chromadb.PersistentClient(path=str(palace)).get_collection("mempalace_drawers")
    assert col.get(where={"source_file": str(f)})["ids"][0].startswith("_reg_")

    with open(f, "a") as fh:
        fh.write("> What is memory?\nMemory is persistence of the exact words.\n\n" * 3)
    out = _mine(src, palace, capsys)
    ids = col.get(where={"source_file": str(f)})["ids"]
    assert _filed(out) == len(ids) > 0
    assert not any(i.startswith("_reg_") for i in ids)


def test_mine_plan(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text("one\n")
    prev = _file_signature(f)
    prev["normalize_version"] = 99
    assert _mine_plan(f, None, prev) == "grow"
    assert _mine_plan(f, prev, _file_signature(f)) == "skip"
    with open(f, "a") as fh:
        fh.write("two\n")
    assert _mine_plan(f, prev, _file_signature(f)) == "grow"
    assert _mine_plan(f, {**prev, "inode": -1}, _file_signature(f)) == "whole"
    assert _mine_plan(f, {**prev, "normalize_version": 0}, _file_signature(f)) == "whole"
    f.write_text("ONE\ntwo\n")
    assert _mine_plan(f, prev, _file_signature(f)) == "whole"
    f.write_text("")
    assert _mine_plan(f, prev, _file_signature(f)) == "whole"


def test_state_file_lives_in_the_palace(convo, capsys):
    src, f, palace = convo
    _mine(src, palace, capsys)
    assert Path(palace, CONVO_STATE_FILE).is_file()
