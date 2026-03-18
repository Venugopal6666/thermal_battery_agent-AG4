"""Simple vector search for rulebook using TF-IDF + cosine similarity.

No external server needed — works entirely in-process.
Falls back gracefully if sklearn is not available.
"""

from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory store keyed by rule_id
_rules_store: dict[str, dict] = {}

# Lazy-loaded vectorizer and matrix
_vectorizer = None
_tfidf_matrix = None
_rule_ids_index: list[str] = []
_needs_rebuild = True


def _rebuild_index():
    """Rebuild the TF-IDF index from the current rules store."""
    global _vectorizer, _tfidf_matrix, _rule_ids_index, _needs_rebuild

    if not _rules_store:
        _vectorizer = None
        _tfidf_matrix = None
        _rule_ids_index = []
        _needs_rebuild = False
        return

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        _rule_ids_index = list(_rules_store.keys())
        documents = [_rules_store[rid]["document"] for rid in _rule_ids_index]

        _vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=5000,
            ngram_range=(1, 2),
        )
        _tfidf_matrix = _vectorizer.fit_transform(documents)
        _needs_rebuild = False
        logger.info(f"Rebuilt TF-IDF index with {len(documents)} rules")
    except ImportError:
        logger.warning("sklearn not installed — semantic search disabled. Install: pip install scikit-learn")
        _needs_rebuild = False
    except Exception as e:
        logger.error(f"Failed to build TF-IDF index: {e}")
        _needs_rebuild = False


def upsert_rule_embedding(
    rule_id: str,
    title: str,
    content: str,
    category: str | None = None,
    tags: list[str] | None = None,
):
    """Add or update a rule in the vector store."""
    global _needs_rebuild
    document_text = f"Title: {title}\n\nContent: {content}"
    _rules_store[rule_id] = {
        "document": document_text,
        "metadata": {
            "title": title,
            "category": category,
            "tags": ",".join(tags) if tags else "",
        },
    }
    _needs_rebuild = True


def delete_rule_embedding(rule_id: str):
    """Delete a rule from the vector store."""
    global _needs_rebuild
    if rule_id in _rules_store:
        del _rules_store[rule_id]
        _needs_rebuild = True


def search_rules_semantic(
    query: str,
    n_results: int = 5,
    category: str | None = None,
) -> list[dict]:
    """Search rules by text similarity.

    Uses TF-IDF + cosine similarity for fast, dependency-light search.
    """
    global _needs_rebuild

    if _needs_rebuild:
        _rebuild_index()

    if not _rules_store or _tfidf_matrix is None or _vectorizer is None:
        # Fall back to simple substring match
        return _fallback_search(query, n_results, category)

    try:
        from sklearn.metrics.pairwise import cosine_similarity

        query_vec = _vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, _tfidf_matrix).flatten()

        # Get top-N indices sorted by similarity
        ranked_indices = similarities.argsort()[::-1]

        results = []
        for idx in ranked_indices:
            if len(results) >= n_results:
                break
            rid = _rule_ids_index[idx]
            rule = _rules_store[rid]
            meta = rule["metadata"]

            # Filter by category if specified
            if category and meta.get("category") != category:
                continue

            score = float(similarities[idx])
            if score < 0.01:  # Skip very low relevance
                continue

            results.append({
                "id": rid,
                "document": rule["document"],
                "metadata": meta,
                "distance": 1 - score,  # Convert similarity to distance
            })

        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return _fallback_search(query, n_results, category)


def _fallback_search(
    query: str,
    n_results: int = 5,
    category: str | None = None,
) -> list[dict]:
    """Simple keyword-based fallback search."""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored = []
    for rid, rule in _rules_store.items():
        meta = rule["metadata"]
        if category and meta.get("category") != category:
            continue

        doc_lower = rule["document"].lower()
        # Score based on word overlap
        doc_words = set(doc_lower.split())
        overlap = len(query_words & doc_words)
        # Bonus for exact substring match
        if query_lower in doc_lower:
            overlap += 5

        if overlap > 0:
            scored.append((rid, rule, overlap))

    # Sort by score descending
    scored.sort(key=lambda x: x[2], reverse=True)

    return [
        {
            "id": rid,
            "document": rule["document"],
            "metadata": rule["metadata"],
            "distance": 1.0 / (1 + score),
        }
        for rid, rule, score in scored[:n_results]
    ]


def rebuild_all_embeddings(rules: list[dict]):
    """Clear and rebuild all embeddings from a list of rule dicts."""
    global _needs_rebuild
    _rules_store.clear()

    for r in rules:
        document_text = f"Title: {r['title']}\n\nContent: {r['content']}"
        metadata = {"title": r["title"]}
        if r.get("category"):
            metadata["category"] = r["category"]
        if r.get("tags"):
            metadata["tags"] = ",".join(r["tags"]) if isinstance(r["tags"], list) else r["tags"]

        _rules_store[str(r["id"])] = {
            "document": document_text,
            "metadata": metadata,
        }

    _needs_rebuild = True
    _rebuild_index()
