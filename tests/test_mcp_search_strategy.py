"""tool_search honours MEMPALACE_CANDIDATE_STRATEGY."""

from mempalace import mcp_server


def _capture(monkeypatch):
    seen = {}

    def fake(query, **kw):
        seen.update(kw)
        return {"query": query, "results": []}

    monkeypatch.setattr(mcp_server, "search_memories", fake)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    return seen


def test_default_strategy_is_vector(monkeypatch):
    monkeypatch.delenv("MEMPALACE_CANDIDATE_STRATEGY", raising=False)
    seen = _capture(monkeypatch)
    mcp_server.tool_search(query="VICE-1624 fix")
    assert seen["candidate_strategy"] == "vector"
    assert seen["max_distance"] == 1.5


def test_union_strategy_drops_default_distance_bound(monkeypatch):
    monkeypatch.setenv("MEMPALACE_CANDIDATE_STRATEGY", "union")
    seen = _capture(monkeypatch)
    mcp_server.tool_search(query="VICE-1624 fix")
    assert seen["candidate_strategy"] == "union"
    assert seen["max_distance"] == 0.0


def test_union_keeps_explicit_similarity_bound(monkeypatch):
    monkeypatch.setenv("MEMPALACE_CANDIDATE_STRATEGY", "union")
    seen = _capture(monkeypatch)
    mcp_server.tool_search(query="VICE-1624 fix", min_similarity=0.5)
    assert seen["max_distance"] == 0.5


def test_unknown_strategy_falls_back_to_vector(monkeypatch):
    monkeypatch.setenv("MEMPALACE_CANDIDATE_STRATEGY", "bogus")
    seen = _capture(monkeypatch)
    mcp_server.tool_search(query="VICE-1624 fix")
    assert seen["candidate_strategy"] == "vector"
