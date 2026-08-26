"""Translate package — stage 5 (Hebrew→English)."""

def __getattr__(name):
    if name in ("main", "call_llm", "mock_translate", "_translate_chunks_with_term_map", "_translate_one_chunk", "_translate_chunk_with_retry", "_glossary_fingerprint", "_is_retryable_chunk_error", "_RETRYABLE_CHUNK_ERRORS", "_yap_root_keys", "_yap_analyze", "_YAP_AVAILABLE"):
        from . import translate as _t
        return getattr(_t, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
