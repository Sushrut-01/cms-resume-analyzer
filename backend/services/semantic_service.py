# =============================================================================
# services/semantic_service.py — Semantic (Meaning-Based) Similarity Scoring
#
# Provides two capabilities used during AI resume analysis:
#
#   1. semantic_score(resume, jd)
#      Overall similarity between resume and JD — measures meaning, not just
#      keyword overlap. A resume can score well even if it uses different words
#      than the JD, as long as the meaning is similar.
#
#   2. detect_soft_gaps(resume, missing_keywords, jd)
#      Classifies each missing keyword as a HARD or SOFT gap:
#        Hard gap = keyword missing AND no semantically related content found
#                   → Candidate genuinely lacks this skill
#        Soft gap = keyword missing BUT resume has a related sentence
#                   → Candidate has the skill, they just need to REPHRASE it
#      This helps recruiters focus rewriting effort on actual gaps vs wording issues.
#
# Two-tier design (automatic fallback):
#   Tier 1 (preferred): sentence-transformers → neural embeddings, ~22 MB model
#                       Best quality — understands context and synonyms
#   Tier 2 (fallback):  scikit-learn TF-IDF   → text overlap scoring
#                       No download needed — works out-of-the-box
#
# All processing runs locally on the server — no text is sent externally.
# HF_HUB_OFFLINE=1 in .env prevents any HuggingFace network calls.
# =============================================================================
import re
import os

# ── Model state ───────────────────────────────────────────────────────────────
_st_model   = None   # sentence-transformers model (Tier 1)
_tier       = None   # "st" | "tfidf" | None

# Tier-1 similarity threshold (neural embeddings, range 0-1)
_SOFT_GAP_THRESHOLD_ST    = 0.62
# Tier-2 similarity threshold (TF-IDF cosine, range 0-1, lower is normal)
_SOFT_GAP_THRESHOLD_TFIDF = 0.18


# -----------------------------------------------------------------------------
# Model Loading (lazy — only loads when first needed, not at import time)
# _load_tier1: tries sentence-transformers (neural, best quality, ~22 MB model)
# _load_tier2: falls back to scikit-learn TF-IDF (keyword overlap, always available)
# Once loaded, the tier is cached in _tier — model is NOT reloaded on each call.
# HF_HUB_OFFLINE=1 in .env prevents any network download — uses local cache only.
# -----------------------------------------------------------------------------

def _load_tier1():
    """Try to load sentence-transformers model. Returns True on success."""
    global _st_model, _tier
    if _tier is not None:
        return _tier == "st"   # already determined — return cached result
    try:
        from sentence_transformers import SentenceTransformer
        # all-MiniLM-L6-v2: fast, small (22 MB), good accuracy for sentence similarity
        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
        _tier = "st"
        print("[semantic] Loaded sentence-transformers (Tier 1 — neural embeddings)")
        return True
    except Exception:
        pass   # package not installed or model not cached — fall through to Tier 2
    return False


def _load_tier2():
    """Try to load scikit-learn. Returns True on success."""
    global _tier
    if _tier is not None:
        return True   # already loaded
    try:
        import sklearn  # noqa: F401
        _tier = "tfidf"
        print("[semantic] sentence-transformers not found — using TF-IDF fallback (Tier 2)")
        return True
    except Exception:
        _tier = "none"   # neither package available — semantic scoring disabled
        return False


def _ensure_loaded():
    """Load the best available tier if not already loaded."""
    if _tier is not None:
        return
    if not _load_tier1():
        _load_tier2()


def get_tier() -> str:
    """Return active tier name: 'st', 'tfidf', or 'none'."""
    _ensure_loaded()
    return _tier or "none"


def is_available() -> bool:
    """Returns True if any semantic scoring tier is available."""
    return get_tier() in ("st", "tfidf")


# ── Similarity helpers ────────────────────────────────────────────────────────

# -----------------------------------------------------------------------------
# Similarity Calculation Helpers
# Cosine similarity measures how similar two vectors are (0 = unrelated, 1 = identical).
# Used to compare resume sentences against missing JD keywords.
# -----------------------------------------------------------------------------

def _cosine(a, b) -> float:
    """Calculate cosine similarity between two vectors. Returns 0.0 to 1.0."""
    import numpy as np
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0   # zero vector has no direction — similarity undefined, return 0
    return float(np.dot(a, b) / (na * nb))


def _st_similarities(query: str, sentences: list) -> list:
    """Tier-1: neural embedding cosine similarities.
    Encodes all texts at once (batched) for efficiency, returns similarity to query."""
    texts = [query] + sentences          # query goes first, sentences follow
    embs  = _st_model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    q_emb = embs[0]                      # first embedding = query
    return [_cosine(q_emb, embs[i + 1]) for i in range(len(sentences))]


def _tfidf_similarities(query: str, sentences: list) -> list:
    """Tier-2: TF-IDF cosine similarities.
    Builds a word frequency matrix and measures overlap between query and each sentence."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    corpus = [query] + sentences
    try:
        # ngram_range=(1,2): considers single words AND word pairs for better matching
        tfidf  = TfidfVectorizer(stop_words="english", min_df=1, ngram_range=(1, 2))
        matrix = tfidf.fit_transform(corpus)
        sims   = cosine_similarity(matrix[0:1], matrix[1:])  # query vs all sentences
        return sims[0].tolist()
    except Exception:
        return [0.0] * len(sentences)   # return zeros if vectorizer fails


def _similarities(query: str, sentences: list) -> list:
    """Route to the correct tier's similarity function."""
    if not sentences:
        return []
    tier = get_tier()
    if tier == "st":
        return _st_similarities(query, sentences)
    if tier == "tfidf":
        return _tfidf_similarities(query, sentences)
    return [0.0] * len(sentences)   # semantic not available — return zeros


def _threshold() -> float:
    """Return the similarity threshold for the active tier.
    ST threshold is higher (0.62) because neural embeddings produce denser scores.
    TF-IDF threshold is lower (0.18) because text overlap scores are naturally smaller."""
    return _SOFT_GAP_THRESHOLD_ST if get_tier() == "st" else _SOFT_GAP_THRESHOLD_TFIDF


# ── Public API ────────────────────────────────────────────────────────────────

def detect_soft_gaps(
    resume_text: str,
    hard_gap_kws: list,
    jd_text: str,
) -> dict:
    """
    Classify each missing keyword as a HARD or SOFT gap.

    Hard gap  → resume has nothing related        → skill genuinely missing
    Soft gap  → resume has a semantically close sentence → needs rephrasing

    Returns:
        {
          "available": bool,
          "tier": "st" | "tfidf" | "none",
          "soft_gaps": [{ keyword, similarity, resume_match, suggestion }, ...],
          "true_gaps": [keyword, ...],   # hard gaps only
          "section_matches": { keyword: { best_sentence, similarity } },
        }
    """
    empty = {"available": False, "tier": "none",
             "soft_gaps": [], "true_gaps": list(hard_gap_kws), "section_matches": {}}

    if not is_available() or not hard_gap_kws:
        empty["tier"] = get_tier()
        return empty

    # Tokenise resume into meaningful sentences / bullets
    resume_sents = [
        s.strip().lstrip("-•* –")
        for s in re.split(r"[\n;]|(?<=[.!?])\s+", resume_text)
        if len(s.strip()) > 20
    ]
    if not resume_sents:
        return {**empty, "available": True, "tier": get_tier()}

    # Precompute JD sentences for context lookup
    jd_sents = [s.strip() for s in re.split(r"[.\n;!?]", jd_text) if len(s.strip()) > 10]

    thresh     = _threshold()
    soft_gaps  = []
    true_gaps  = []
    sec_matches = {}

    for kw in hard_gap_kws:
        # Build query: JD sentence mentioning keyword (more context than bare keyword)
        kw_lower    = kw.lower()
        jd_ctx      = next((s for s in jd_sents if kw_lower in s.lower()), kw)
        sims        = _similarities(jd_ctx, resume_sents)

        best_idx  = int(max(range(len(sims)), key=lambda i: sims[i])) if sims else 0
        best_sim  = round(sims[best_idx], 2) if sims else 0.0
        best_sent = resume_sents[best_idx] if resume_sents else ""

        sec_matches[kw] = {"best_sentence": best_sent[:130], "similarity": best_sim}

        if best_sim >= thresh:
            short = best_sent[:70] + ("…" if len(best_sent) > 70 else "")
            soft_gaps.append({
                "keyword":      kw,
                "similarity":   best_sim,
                "resume_match": best_sent[:130],
                "suggestion":   f"Add '{kw}' explicitly. Your resume says: \"{short}\" — rephrase to include '{kw}'.",
            })
        else:
            true_gaps.append(kw)

    return {
        "available":      True,
        "tier":           get_tier(),
        "soft_gaps":      soft_gaps,
        "true_gaps":      true_gaps,
        "section_matches": sec_matches,
    }


def semantic_score(resume_text: str, jd_text: str):
    """
    Overall semantic similarity between resume and JD (0-100).
    Complements the ATS keyword score — measures meaning, not just word overlap.
    Returns None if semantic service unavailable.
    """
    if not is_available():
        return None
    # Use the most informative portion of each document
    res_snip = resume_text[:2500]
    jd_snip  = jd_text[:2500]
    sims = _similarities(jd_snip, [res_snip])
    if not sims:
        return None
    return round(sims[0] * 100)


def warmup():
    """Call at server startup to pre-load the model in the background."""
    _ensure_loaded()
