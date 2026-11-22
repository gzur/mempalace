"""Embedding function factory with hardware acceleration.

Returns a ChromaDB-compatible embedding function bound to a user-selected
ONNX Runtime execution provider. The same ``all-MiniLM-L6-v2`` model and
384-dim vectors ChromaDB ships by default are reused, so switching device
does not invalidate existing palaces.

Supported devices (env ``MEMPALACE_EMBEDDING_DEVICE`` or ``embedding_device``
in ``~/.mempalace/config.json``):

* ``auto`` — prefer CUDA ▸ CoreML ▸ DirectML, fall back to CPU
* ``cpu`` — force CPU (the historical default)
* ``cuda`` — NVIDIA GPU via ``onnxruntime-gpu`` (``pip install mempalace[gpu]``)
* ``coreml`` — Apple Neural Engine (macOS)
* ``dml`` — DirectML (Windows / AMD / Intel GPUs)

Requesting an unavailable accelerator emits a warning and falls back to CPU
rather than hard-failing — mining must still work on a laptop without CUDA.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PROVIDER_MAP = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "coreml": ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
}

_DEVICE_EXTRA = {
    "cuda": "mempalace[gpu]",
    "coreml": "mempalace[coreml]",
    "dml": "mempalace[dml]",
}

_AUTO_ORDER = [
    ("CUDAExecutionProvider", "cuda"),
    ("CoreMLExecutionProvider", "coreml"),
    ("DmlExecutionProvider", "dml"),
]

_EF_CACHE: dict = {}
_WARNED: set = set()


def _resolve_providers(device: str) -> tuple[list, str]:
    """Return ``(provider_list, effective_device)`` for ``device``.

    Falls back to CPU (with a one-shot warning) when the requested
    accelerator is not compiled into the installed ``onnxruntime``.
    """
    device = (device or "auto").strip().lower()

    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
    except ImportError:
        return (["CPUExecutionProvider"], "cpu")

    if device == "auto":
        for provider, name in _AUTO_ORDER:
            if provider in available:
                return ([provider, "CPUExecutionProvider"], name)
        return (["CPUExecutionProvider"], "cpu")

    requested = _PROVIDER_MAP.get(device)
    if requested is None:
        if device not in _WARNED:
            logger.warning("Unknown embedding_device %r — falling back to cpu", device)
            _WARNED.add(device)
        return (["CPUExecutionProvider"], "cpu")

    preferred = requested[0]
    if preferred == "CPUExecutionProvider":
        return (requested, "cpu")

    if preferred not in available:
        if device not in _WARNED:
            extra = _DEVICE_EXTRA.get(device, "the matching mempalace extra for your device")
            logger.warning(
                "embedding_device=%r requested but %s is not installed — "
                "falling back to CPU. Install %s.",
                device,
                preferred,
                extra,
            )
            _WARNED.add(device)
        return (["CPUExecutionProvider"], "cpu")

    return (requested, device)


def _build_ef_class():
    """Subclass ``ONNXMiniLM_L6_V2`` with name ``"default"``.

    Why the rename: ChromaDB 1.5 persists the EF identity on the collection
    and rejects reads that pass a differently-named EF (``onnx_mini_lm_l6_v2``
    vs ``default``). The vectors and model are identical — only the
    ``name()`` tag differs — so spoofing the name lets one EF class serve
    palaces created with ``DefaultEmbeddingFunction`` *and* palaces we
    create ourselves, with the same GPU-capable ``preferred_providers``.
    """
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    class _MempalaceONNX(ONNXMiniLM_L6_V2):
        @staticmethod
        def name() -> str:
            return "default"

    return _MempalaceONNX


DEFAULT_MODEL = "minilm"

# Query-side instructions for asymmetric retrieval models. Documents are
# embedded as is; only the search query gets the prefix.
_QUERY_PREFIXES = {
    "bge-": "Represent this sentence for searching relevant passages: ",
    "embeddinggemma": "task: search result | query: ",
    "nomic-embed": "search_query: ",
}
_DOC_PREFIXES = {
    "embeddinggemma": "title: none | text: ",
    "nomic-embed": "search_document: ",
}


def _prefix_for(model_name: str, table: dict) -> str:
    lowered = model_name.lower()
    for key, prefix in table.items():
        if key in lowered:
            return prefix
    return ""


def _st_device(device: str) -> str:
    """Map an ``embedding_device`` value to a torch device string."""
    device = (device or "auto").strip().lower()
    if device in ("cpu", "cuda", "mps"):
        return device
    if device == "coreml":
        device = "auto"
    try:
        import torch

        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available():
                return "mps"
    except ImportError:
        pass
    return "cpu"


def _build_st_ef_class():
    """Sentence-transformers EF that keeps ChromaDB's registered name.

    ``name()`` stays ``"sentence_transformer"`` so ChromaDB can resolve the
    persisted collection config. On top of the stock class it adds the
    query/document prefixes that asymmetric models (bge, embeddinggemma,
    nomic) need, and L2-normalized vectors for cosine search.
    """
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    class _MempalaceST(SentenceTransformerEmbeddingFunction):
        def __init__(self, model_name: str, device: str):
            super().__init__(model_name=model_name, device=device, normalize_embeddings=True)
            self._query_prefix = _prefix_for(model_name, _QUERY_PREFIXES)
            self._doc_prefix = _prefix_for(model_name, _DOC_PREFIXES)

        def __call__(self, input):
            if self._doc_prefix:
                input = [self._doc_prefix + d for d in input]
            return super().__call__(input)

        def embed_query(self, input):
            if self._query_prefix:
                input = [self._query_prefix + q for q in input]
            return super().__call__(input)

    return _MempalaceST


def get_embedding_function(device: Optional[str] = None, model: Optional[str] = None):
    """Return a cached embedding function bound to the requested device.

    ``device=None`` reads from :class:`MempalaceConfig.embedding_device`.
    ``model=None`` reads from :class:`MempalaceConfig.embedding_model`;
    ``"minilm"`` is the ONNX default, any other value is a local
    sentence-transformers model. The returned function is shared across
    calls with the same resolved settings so we only pay model-load cost
    once per process.
    """
    if device is None or model is None:
        from .config import MempalaceConfig

        cfg = MempalaceConfig()
        device = cfg.embedding_device if device is None else device
        model = cfg.embedding_model if model is None else model

    if model and model.lower() != DEFAULT_MODEL:
        st_device = _st_device(device)
        cache_key = ("st", model, st_device)
        cached = _EF_CACHE.get(cache_key)
        if cached is not None:
            return cached
        ef = _build_st_ef_class()(model_name=model, device=st_device)
        _EF_CACHE[cache_key] = ef
        logger.info("Embedding function initialized (model=%s device=%s)", model, st_device)
        return ef

    providers, effective = _resolve_providers(device)
    cache_key = tuple(providers)
    cached = _EF_CACHE.get(cache_key)
    if cached is not None:
        return cached

    ef_cls = _build_ef_class()
    ef = ef_cls(preferred_providers=providers)
    _EF_CACHE[cache_key] = ef
    logger.info("Embedding function initialized (device=%s providers=%s)", effective, providers)
    return ef


def describe_device(device: Optional[str] = None) -> str:
    """Return a short human-readable label for the resolved device.

    Used by the miner CLI header so users can see at a glance whether GPU
    acceleration actually engaged.
    """
    if device is None:
        from .config import MempalaceConfig

        device = MempalaceConfig().embedding_device
    _, effective = _resolve_providers(device)
    return effective
