"""
query_sanitizer.py — Mitigate system prompt contamination in search queries.

Problem: AI agents sometimes prepend system prompts (2000+ chars) to search queries.
Embedding models represent the concatenated string as a single vector where the
system prompt overwhelms the actual question (typically 10-50 chars), causing
near-total retrieval failure (89.8% → 1.0% R@10). See Issue #333.

Approach: "Mitigation" (減災) — not perfect prevention, but prevents the cliff.

Expected recovery:
  Step 1 passthrough (≤200 chars)     → no degradation, ~89.8%
  Step 2 question extraction (？found) → near-full recovery, ~85-89%
  Step 3 tail sentence extraction      → moderate recovery, ~80-89%
  Step 4 tail truncation (fallback)    → minimum viable, ~70-80%

  Without sanitizer: 1.0% (catastrophic silent failure)
  Worst case with sanitizer: ~70-80% (survivable)
"""

import re
import logging

logger = logging.getLogger("mempalace_mcp")

# --- Constants ---
MAX_QUERY_LENGTH = 250  # Above this, prompt contamination increasingly dominates
SAFE_QUERY_LENGTH = 200  # Below this, query is almost certainly clean
MIN_QUERY_LENGTH = 10  # Extracted result shorter than this = extraction failed
QUOTE_CHARS = {"'", '"'}

# Sentence splitter: split on . ! ? (including fullwidth) and newlines
_SENTENCE_SPLIT = re.compile(r"[.!?。！？\n]+")

# Question detector: ends with ? or ？ (possibly with trailing whitespace/quotes)
_QUESTION_MARK = re.compile(r'[?？]\s*["\']?\s*$')


def sanitize_query(raw_query: str) -> dict:
    """
    Extract the actual search intent from a potentially contaminated query.

    Args:
        raw_query: The raw query string from the AI agent, possibly containing
                   system prompt content prepended to the actual question.

    Returns:
        dict with keys:
            clean_query (str): The sanitized query to use for embedding search
            was_sanitized (bool): Whether any sanitization was applied
            original_length (int): Length of the raw input
            clean_length (int): Length of the sanitized output
            method (str): Which extraction method was used
                - "passthrough": query was short enough, no action taken
                - "question_extraction": found and extracted a question sentence
                - "tail_sentence": extracted the last meaningful sentence
                - "tail_truncation": fallback — took the last MAX_QUERY_LENGTH chars
    """
    if not raw_query or not raw_query.strip():
        return {
            "clean_query": raw_query or "",
            "was_sanitized": False,
            "original_length": len(raw_query) if raw_query else 0,
            "clean_length": len(raw_query) if raw_query else 0,
            "method": "passthrough",
        }

    raw_query = raw_query.strip()
    original_length = len(raw_query)

    def _strip_wrapping_quotes(candidate: str) -> str:
        candidate = candidate.strip()
        while (
            len(candidate) >= 2 and candidate[:1] in QUOTE_CHARS and candidate[:1] == candidate[-1:]
        ):
            candidate = candidate[1:-1].strip()
            if not candidate:
                return ""
        if candidate[:1] in QUOTE_CHARS:
            candidate = candidate[1:].strip()
        if candidate[-1:] in QUOTE_CHARS:
            candidate = candidate[:-1].strip()
        return candidate

    def _trim_candidate(candidate: str) -> str:
        candidate = _strip_wrapping_quotes(candidate)
        if len(candidate) <= MAX_QUERY_LENGTH:
            return candidate

        nested_fragments = [
            _strip_wrapping_quotes(frag)
            for frag in _SENTENCE_SPLIT.split(candidate)
            if frag.strip()
        ]
        for frag in reversed(nested_fragments):
            if MIN_QUERY_LENGTH <= len(frag) <= MAX_QUERY_LENGTH:
                return frag

        return candidate[-MAX_QUERY_LENGTH:].strip()

    # --- Step 1: Short query passthrough ---
    if original_length <= SAFE_QUERY_LENGTH:
        return {
            "clean_query": raw_query,
            "was_sanitized": False,
            "original_length": original_length,
            "clean_length": original_length,
            "method": "passthrough",
        }

    # --- Step 2: Question extraction ---
    # Split into sentences and find ones ending with ?
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(raw_query) if s.strip()]

    # Also split on newlines to catch questions on their own line
    all_segments = []
    for s in raw_query.split("\n"):
        s = s.strip()
        if s:
            all_segments.append(s)

    # Look for question marks in segments (prefer later ones = more likely the actual query)
    question_sentences = []
    for seg in reversed(all_segments):
        if _QUESTION_MARK.search(seg):
            question_sentences.append(seg)

    if not question_sentences:
        # Also check the sentence-split results
        for sent in reversed(sentences):
            if "?" in sent or "？" in sent:
                question_sentences.append(sent)

    if question_sentences:
        # Take the last (most recent) question found
        candidate = question_sentences[0].strip()
        if len(candidate) >= MIN_QUERY_LENGTH:
            # Apply length guard
            if len(candidate) > MAX_QUERY_LENGTH:
                candidate = _trim_candidate(candidate)
            logger.warning(
                "Query sanitized: %d → %d chars (method=question_extraction)",
                original_length,
                len(candidate),
            )
            return {
                "clean_query": candidate,
                "was_sanitized": True,
                "original_length": original_length,
                "clean_length": len(candidate),
                "method": "question_extraction",
            }

    # --- Step 3: Tail sentence extraction ---
    # System prompts are prepended, so the actual query is near the end.
    # Walk backwards through segments to find the last meaningful sentence.
    for seg in reversed(all_segments):
        seg = seg.strip()
        if len(seg) >= MIN_QUERY_LENGTH:
            candidate = _trim_candidate(seg)
            if len(candidate) < MIN_QUERY_LENGTH:
                continue
            logger.warning(
                "Query sanitized: %d → %d chars (method=tail_sentence)",
                original_length,
                len(candidate),
            )
            return {
                "clean_query": candidate,
                "was_sanitized": True,
                "original_length": original_length,
                "clean_length": len(candidate),
                "method": "tail_sentence",
            }

    # --- Step 4: Tail truncation (fallback) ---
    # Nothing worked — just take the last MAX_QUERY_LENGTH characters.
    candidate = raw_query[-MAX_QUERY_LENGTH:].strip()
    logger.warning(
        "Query sanitized: %d → %d chars (method=tail_truncation)", original_length, len(candidate)
    )
    return {
        "clean_query": candidate,
        "was_sanitized": True,
        "original_length": original_length,
        "clean_length": len(candidate),
        "method": "tail_truncation",
    }


# --- Prompt-hook query builder ---

HOOK_QUERY_LENGTH = SAFE_QUERY_LENGTH  # stays under the passthrough limit
_MAX_KEY_TERMS = 8
_KEY_TERM_PATTERNS = [
    re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b"),  # ticket keys: VICE-2085, CVE-2026-1
    re.compile(r"(?<![\w!])![0-9]{2,6}\b"),  # GitLab MR: !1648
    re.compile(r"(?<![\w@])@[a-z][a-z0-9_-]{2,30}\b"),  # agent names: @dora, @mpinject-
    re.compile(
        r"\b[\w-]+\.(?:go|py|ts|tsx|js|md|ya?ml|json|sh|toml|tf|sql|rs)(?::\d+)?\b"
    ),  # file names, with optional :line
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\(\)"),  # function calls: foo()
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),  # camelCase identifiers
    re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+){1,}\b"),  # snake_case identifiers
    re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b"),  # git SHAs
]
_MD_NOISE = re.compile(r"^[#>*\-\s`|]+")


def extract_key_terms(text: str, limit: int = _MAX_KEY_TERMS) -> list:
    """Return distinctive literal terms of ``text`` in order of first use.

    Ticket keys, MR numbers, agent names, file names, function names,
    identifiers and SHAs are what BM25 matches exactly and what an
    embedding of a long brief washes out.
    """
    found = []
    for pattern in _KEY_TERM_PATTERNS:
        for m in pattern.finditer(text):
            found.append((m.start(), m.group(0).rstrip("()")))
    found.sort()
    terms, seen = [], set()
    for _, term in found:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    # Drop a term that is part of a longer one (``foo`` inside ``foo.go:12``).
    lowered = [t.lower() for t in terms]
    out = [t for t, k in zip(terms, lowered) if not any(k != o and k in o for o in lowered)]
    return out[:limit]


def _cut_words(text: str, length: int) -> str:
    """Cut ``text`` to at most ``length`` chars on a word boundary."""
    if length <= 0:
        return ""
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut if cut else text[:length]


def _meaningful_sentences(text: str) -> list:
    out = []
    for line in text.split("\n"):
        line = _MD_NOISE.sub("", line).strip()
        if not line:
            continue
        for sent in re.split(r"(?<=[.!?])\s+", line):
            sent = sent.strip()
            if len(sent) >= MIN_QUERY_LENGTH and len(sent.split()) >= 3:
                out.append(sent)
    return out


def build_prompt_query(prompt: str, max_length: int = HOOK_QUERY_LENGTH) -> dict:
    """Build a search query from a whole prompt for the prompt hook.

    A short prompt passes through. A long one (a brief) becomes its key
    terms plus its first and last meaningful sentences, cut to
    ``max_length`` so the search path does not trim it again. The old
    hook searched only the last sentence, which for a brief is often
    "send your result back via hcom".
    """
    prompt = (prompt or "").strip()
    if len(prompt) <= max_length:
        return {"query": prompt, "method": "passthrough", "key_terms": extract_key_terms(prompt)}

    terms = extract_key_terms(prompt)
    term_part = _cut_words(" ".join(terms), max_length * 9 // 20)
    sentences = _meaningful_sentences(prompt)
    first = sentences[0] if sentences else ""
    last = sentences[-1] if len(sentences) > 1 else ""
    room = max_length - len(term_part) - (1 if term_part else 0)
    if last:
        first = _cut_words(first, max(room // 2, room - len(last) - 1))
        last = _cut_words(last, room - len(first) - 1)
    else:
        first = _cut_words(first, room)
    query = " ".join(p for p in (term_part, first, last) if p).strip()
    return {"query": query, "method": "key_terms+first+last", "key_terms": terms}
