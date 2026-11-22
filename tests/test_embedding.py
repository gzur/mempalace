import pytest

import mempalace.embedding as embedding


@pytest.fixture(autouse=True)
def isolate_embedding_state(monkeypatch):
    monkeypatch.setattr(embedding, "_EF_CACHE", {})
    monkeypatch.setattr(embedding, "_WARNED", set())


def test_auto_picks_cuda(monkeypatch):
    monkeypatch.setattr(
        "onnxruntime.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    assert embedding._resolve_providers("auto") == (
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        "cuda",
    )


def test_auto_falls_to_cpu(monkeypatch):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert embedding._resolve_providers("auto") == (["CPUExecutionProvider"], "cpu")


def test_cuda_missing_warns_with_gpu_extra(monkeypatch, caplog):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert embedding._resolve_providers("cuda") == (["CPUExecutionProvider"], "cpu")
    assert "mempalace[gpu]" in caplog.text


def test_coreml_missing_warns_with_coreml_extra(monkeypatch, caplog):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert embedding._resolve_providers("coreml") == (["CPUExecutionProvider"], "cpu")
    assert "mempalace[coreml]" in caplog.text


def test_dml_missing_warns_with_dml_extra(monkeypatch, caplog):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert embedding._resolve_providers("dml") == (["CPUExecutionProvider"], "cpu")
    assert "mempalace[dml]" in caplog.text


def test_unknown_device_warns_once(monkeypatch, caplog):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert embedding._resolve_providers("bogus") == (["CPUExecutionProvider"], "cpu")
    assert embedding._resolve_providers("bogus") == (["CPUExecutionProvider"], "cpu")
    assert caplog.text.count("Unknown embedding_device") == 1


def test_onnxruntime_import_error_falls_back_to_cpu(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert embedding._resolve_providers("cuda") == (["CPUExecutionProvider"], "cpu")


def test_get_embedding_function_caches_by_resolved_provider_tuple(monkeypatch):
    class DummyEF:
        def __init__(self, preferred_providers):
            self.preferred_providers = preferred_providers

    monkeypatch.setattr(embedding, "_build_ef_class", lambda: DummyEF)
    monkeypatch.setattr(
        embedding, "_resolve_providers", lambda device: (["CPUExecutionProvider"], "cpu")
    )

    first = embedding.get_embedding_function("cpu")
    second = embedding.get_embedding_function("auto")

    assert first is second
    assert first.preferred_providers == ["CPUExecutionProvider"]


def test_describe_device_uses_resolved_effective_device(monkeypatch):
    monkeypatch.setattr(
        embedding,
        "_resolve_providers",
        lambda device: (["CUDAExecutionProvider", "CPUExecutionProvider"], "cuda"),
    )

    assert embedding.describe_device("auto") == "cuda"


def test_embedding_model_defaults_to_minilm(monkeypatch, tmp_path):
    from mempalace.config import MempalaceConfig

    monkeypatch.delenv("MEMPALACE_EMBEDDING_MODEL", raising=False)
    assert MempalaceConfig(config_dir=str(tmp_path)).embedding_model == "minilm"


def test_embedding_model_env_overrides(monkeypatch, tmp_path):
    from mempalace.config import MempalaceConfig

    monkeypatch.setenv("MEMPALACE_EMBEDDING_MODEL", " BAAI/bge-base-en-v1.5 ")
    assert MempalaceConfig(config_dir=str(tmp_path)).embedding_model == "BAAI/bge-base-en-v1.5"


def test_query_prefix_by_model_family():
    assert embedding._prefix_for("BAAI/bge-base-en-v1.5", embedding._QUERY_PREFIXES).startswith(
        "Represent this sentence"
    )
    assert embedding._prefix_for("google/embeddinggemma-300m", embedding._QUERY_PREFIXES) == (
        "task: search result | query: "
    )
    assert (
        embedding._prefix_for("sentence-transformers/all-mpnet-base-v2", embedding._QUERY_PREFIXES)
        == ""
    )


def test_st_model_builds_sentence_transformer_ef(monkeypatch):
    built = {}

    class FakeST:
        def __init__(self, model_name, device):
            built["args"] = (model_name, device)

    monkeypatch.setattr(embedding, "_build_st_ef_class", lambda: FakeST)
    ef = embedding.get_embedding_function(device="cpu", model="BAAI/bge-small-en-v1.5")
    assert isinstance(ef, FakeST)
    assert built["args"] == ("BAAI/bge-small-en-v1.5", "cpu")
    # cached on the second call
    assert embedding.get_embedding_function(device="cpu", model="BAAI/bge-small-en-v1.5") is ef


def test_minilm_model_keeps_onnx_path(monkeypatch):
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])
    ef = embedding.get_embedding_function(device="cpu", model="minilm")
    assert ef.name() == "default"


def test_st_ef_adds_query_prefix_only_to_queries():
    pytest.importorskip("sentence_transformers")
    cls = embedding._build_st_ef_class()
    seen = []
    ef = cls.__new__(cls)
    ef._query_prefix = "Q: "
    ef._doc_prefix = ""
    ef._model = type(
        "M", (), {"encode": lambda self, x, **k: seen.append(list(x)) or [[0.0] * 3 for _ in x]}
    )()
    ef.normalize_embeddings = True
    ef.embed_query(["find x"])
    ef(["doc y"])
    assert seen == [["Q: find x"], ["doc y"]]
