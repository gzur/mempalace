"""Unit tests for convo_miner pure functions (no chromadb needed)."""

import contextlib
from pathlib import Path

from mempalace.convo_miner import (
    _file_chunks_locked,
    chunk_exchanges,
    detect_convo_room,
    scan_convos,
)


class TestChunkExchanges:
    def test_exchange_chunking(self):
        content = (
            "> What is memory?\n"
            "Memory is persistence of information over time.\n\n"
            "> Why does it matter?\n"
            "It enables continuity across sessions and conversations.\n\n"
            "> How do we build it?\n"
            "With structured storage and retrieval mechanisms.\n"
        )
        chunks = chunk_exchanges(content)
        assert len(chunks) >= 2
        assert all("content" in c and "chunk_index" in c for c in chunks)

    def test_paragraph_fallback(self):
        """Content without '>' lines falls back to paragraph chunking."""
        content = (
            "This is a long paragraph about memory systems. " * 10 + "\n\n"
            "This is another paragraph about storage. " * 10 + "\n\n"
            "And a third paragraph about retrieval. " * 10
        )
        chunks = chunk_exchanges(content)
        assert len(chunks) >= 2

    def test_paragraph_line_group_fallback(self):
        """Long content with no paragraph breaks chunks by line groups."""
        lines = [f"Line {i}: some content that is meaningful" for i in range(60)]
        content = "\n".join(lines)
        chunks = chunk_exchanges(content)
        assert len(chunks) >= 1

    def test_giant_single_paragraph_splits_to_chunk_size(self):
        # Regression for self-u7o: an ~8MB single-paragraph SQL dump used
        # to land as one drawer because _chunk_by_paragraph emitted any
        # paragraph above MIN_CHUNK_SIZE whole. The hard size cap inside
        # the paragraph loop now splits paragraphs at CHUNK_SIZE.
        from mempalace.convo_miner import CHUNK_SIZE

        blob = "INSERT INTO foo VALUES (1, 'data'); " * 30_000  # ~1.05 MB
        chunks = chunk_exchanges(blob)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c["content"]) <= CHUNK_SIZE

    def test_empty_content(self):
        chunks = chunk_exchanges("")
        assert chunks == []

    def test_short_content_skipped(self):
        chunks = chunk_exchanges("> hi\nbye")
        # Too short to produce chunks (below MIN_CHUNK_SIZE)
        assert isinstance(chunks, list)

    def test_long_ai_response_not_truncated(self):
        """AI responses longer than 8 lines must be stored in full (verbatim principle)."""
        lines = [f"Step {i}: important detail that must be stored" for i in range(1, 14)]
        content = "> How do I implement authentication?\n" + "\n".join(lines)
        chunks = chunk_exchanges(content)
        assert len(chunks) >= 1
        stored = chunks[0]["content"]
        # All 13 lines must be present — none silently dropped
        for i in range(1, 14):
            assert f"Step {i}:" in stored, f"Step {i} was truncated and not stored"


class TestDetectConvoRoom:
    def test_technical_room(self):
        content = "Let me debug this python function and fix the code error in the api"
        assert detect_convo_room(content) == "technical"

    def test_planning_room(self):
        content = "We need to plan the roadmap for the next sprint and set milestone deadlines"
        assert detect_convo_room(content) == "planning"

    def test_architecture_room(self):
        content = "The architecture uses a service layer with component interface and module design"
        assert detect_convo_room(content) == "architecture"

    def test_decisions_room(self):
        content = "We decided to switch and migrated to the new framework after we chose it"
        assert detect_convo_room(content) == "decisions"

    def test_general_fallback(self):
        content = "Hello, how are you doing today? The weather is nice."
        assert detect_convo_room(content) == "general"


class TestScanConvos:
    def test_scan_finds_txt_and_md(self, tmp_path):
        (tmp_path / "chat.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "notes.md").write_text("world", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"fake")
        files = scan_convos(str(tmp_path))
        extensions = {f.suffix for f in files}
        assert ".txt" in extensions
        assert ".md" in extensions
        assert ".png" not in extensions

    def test_scan_skips_git_dir(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config.txt").write_text("git stuff", encoding="utf-8")
        (tmp_path / "chat.txt").write_text("hello", encoding="utf-8")
        files = scan_convos(str(tmp_path))
        assert len(files) == 1

    def test_scan_skips_tool_results_dir(self, tmp_path):
        # Regression for self-u7o: Claude Code spills oversize tool
        # outputs (Supabase dumps, MCP responses) into a tool-results/
        # subdir. The parent transcript already cites them, so mining
        # them again just produces megablob drawers.
        tr = tmp_path / "tool-results"
        tr.mkdir()
        (tr / "supabase-dump.txt").write_text("INSERT INTO foo VALUES (1);" * 10000)
        (tmp_path / "session.jsonl").write_text(
            '{"role":"user","content":"hi"}\n', encoding="utf-8"
        )
        files = scan_convos(str(tmp_path))
        names = [f.name for f in files]
        assert "session.jsonl" in names
        assert "supabase-dump.txt" not in names

    def test_scan_skips_meta_json(self, tmp_path):
        (tmp_path / "chat.meta.json").write_text("{}", encoding="utf-8")
        (tmp_path / "chat.json").write_text("{}", encoding="utf-8")
        files = scan_convos(str(tmp_path))
        names = [f.name for f in files]
        assert "chat.json" in names
        assert "chat.meta.json" not in names

    def test_scan_empty_dir(self, tmp_path):
        files = scan_convos(str(tmp_path))
        assert files == []


class TestFileChunksLocked:
    def test_uses_bounded_upsert_batches(self, monkeypatch):
        import mempalace.convo_miner as convo_miner

        class FakeCol:
            def __init__(self):
                self.batch_sizes = []

            def delete(self, *args, **kwargs):
                pass

            def upsert(self, documents, ids, metadatas):
                self.batch_sizes.append(len(documents))

        chunks = [{"content": f"chunk {i} " * 20, "chunk_index": i} for i in range(5)]
        col = FakeCol()
        monkeypatch.setattr(convo_miner, "DRAWER_UPSERT_BATCH_SIZE", 2)
        monkeypatch.setattr(
            convo_miner, "file_already_mined", lambda collection, source_file: False
        )
        monkeypatch.setattr(convo_miner, "mine_lock", lambda source_file: contextlib.nullcontext())
        monkeypatch.setattr(convo_miner, "_detect_hall_cached", lambda content: "conversations")

        drawers, room_counts, skipped = _file_chunks_locked(
            col, "chat.txt", chunks, "wing", "general", "agent", "exchange"
        )

        assert drawers == 5
        assert dict(room_counts) == {}
        assert skipped is False
        assert col.batch_sizes == [2, 2, 1]


def test_message_timestamps_reads_sorted_jsonl_dates(tmp_path):
    from mempalace.convo_miner import _message_timestamps

    f = tmp_path / "s.jsonl"
    f.write_text(
        '{"type":"user","timestamp":"2026-09-11T12:01:26.123Z"}\n'
        '{"type":"summary"}\n'
        "not json\n"
        '{"type":"assistant","timestamp":"2026-09-10T22:20:18Z"}\n',
        encoding="utf-8",
    )
    assert _message_timestamps(f) == ["2026-09-10T22:20:18", "2026-09-11T12:01:26"]
    other = tmp_path / "s.txt"
    other.write_text('"timestamp":"2026-09-10T22:20:18Z"', encoding="utf-8")
    assert _message_timestamps(other) == []


def test_date_chunks_spreads_timestamps_in_order():
    from mempalace.convo_miner import _date_chunks

    chunks = [{"content": str(i)} for i in range(4)]
    _date_chunks(chunks, ["2026-01-01T00:00:00", "2026-01-02T00:00:00"])
    assert [c["source_date"][:10] for c in chunks] == [
        "2026-01-01",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02",
    ]
    assert all(c["session_end"] == "2026-01-02T00:00:00" for c in chunks)
    assert all(c["date_source"] == "estimate" for c in chunks)
    empty = [{"content": "x"}]
    _date_chunks(empty, [])
    assert "source_date" not in empty[0]


FIXTURE = Path(__file__).parent / "fixtures" / "claude_session_dated.jsonl"


def test_normalize_with_times_matches_normalize_and_dates_each_line():
    from mempalace.normalize import normalize, normalize_with_times

    content, times = normalize_with_times(str(FIXTURE))
    assert content == normalize(str(FIXTURE))
    lines = content.split("\n")
    assert len(times) == len(lines)
    by_line = dict(zip(lines, times))
    assert by_line["> What is the minimum cluster floor on Azure for the shrink path?"] == (
        "2026-09-01T09:00:00"
    )
    assert by_line[
        "> Make the nightly job block the pipeline now, please, with the ticket VICE-2192."
    ] == ("2026-09-17T15:00:00")
    # separator lines carry no time
    assert all(t is None for line, t in zip(lines, times) if line == "")


def test_normalize_with_times_is_none_for_plain_text(tmp_path):
    from mempalace.normalize import normalize_with_times

    f = tmp_path / "chat.txt"
    f.write_text("> one question here\nanswer\n\n> two\nb\n\n> three\nc\n", encoding="utf-8")
    content, times = normalize_with_times(str(f))
    assert times is None
    assert content.startswith("> one question")


def test_drawer_dates_come_from_their_own_records():
    """The position estimate puts exchange 1 on 2026-09-02; the record says 09-01."""
    from mempalace.convo_miner import _date_chunks, _message_timestamps, chunk_exchanges
    from mempalace.normalize import normalize_with_times

    content, times = normalize_with_times(str(FIXTURE))
    chunks = chunk_exchanges(content, with_lines=True)
    estimate = [dict(c) for c in chunks]
    for c in estimate:
        c.pop("lines")
    _date_chunks(estimate, _message_timestamps(FIXTURE))
    assert estimate[0]["source_date"][:10] != "2026-09-01"  # the old bug

    _date_chunks(chunks, _message_timestamps(FIXTURE), times)
    got = [(c["source_date"], c.get("source_date_end"), c["date_source"]) for c in chunks]
    assert got == [
        ("2026-09-01T09:00:00", "2026-09-01T09:00:30", "record"),
        ("2026-09-02T10:00:00", "2026-09-02T10:01:00", "record"),
        ("2026-09-17T15:00:00", "2026-09-17T15:30:00", "record"),
    ]
    assert all(c["session_end"] == "2026-09-17T15:30:00" for c in chunks)
    assert all("lines" not in c for c in chunks)


def test_long_exchange_split_dates_each_part_from_its_lines():
    from mempalace.convo_miner import _date_chunks, chunk_exchanges

    body = [f"line {i} " + "x" * 300 for i in range(4)]
    content = (
        "> question one about the floor\n"
        + "\n".join(body)
        + "\n\n> q2 is here now\nok fine answer\n\n> q3 is here now\nok"
    )
    times = ["2026-01-01T00:00:00"] + [f"2026-01-0{i + 2}T00:00:00" for i in range(4)]
    times += [None, "2026-02-01T00:00:00", "2026-02-01T00:00:01", None, "2026-03-01T00:00:00", "x"]
    times = times[: content.count("\n") + 1]
    chunks = chunk_exchanges(content, with_lines=True)
    _date_chunks(chunks, [], times)
    assert chunks[0]["source_date"] == "2026-01-01T00:00:00"
    # the continuation drawer starts inside the later lines, not at the user turn
    assert chunks[1]["source_date"] > "2026-01-02"
    assert chunks[1]["date_source"] == "record"


def test_paragraph_chunks_get_record_dates(tmp_path):
    from mempalace.convo_miner import _date_chunks, chunk_exchanges

    content = "first paragraph of the plan, long enough\n\nsecond paragraph with more words here"
    times = ["2026-05-01T00:00:00", None, "2026-05-03T00:00:00"]
    chunks = chunk_exchanges(content, with_lines=True)
    _date_chunks(chunks, [], times)
    assert [c["source_date"] for c in chunks] == ["2026-05-01T00:00:00", "2026-05-03T00:00:00"]


def test_mine_convos_stores_record_dates(tmp_path):
    import shutil

    import chromadb

    from mempalace.convo_miner import mine_convos

    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIXTURE, src / "session.jsonl")
    palace = tmp_path / "palace"
    mine_convos(str(src), str(palace), wing="t")
    col = chromadb.PersistentClient(path=str(palace)).get_collection("mempalace_drawers")
    metas = [
        m for m in col.get(include=["metadatas"])["metadatas"] if m.get("chunk_index") is not None
    ]
    dated = sorted(
        (m["chunk_index"], m["source_date"], m["date_source"]) for m in metas if "source_date" in m
    )
    assert dated == [
        (0, "2026-09-01T09:00:00", "record"),
        (1, "2026-09-02T10:00:00", "record"),
        (2, "2026-09-17T15:00:00", "record"),
    ]
