"""Rulebook tools for the ADK agent to search and retrieve rules from ChromaDB."""

from services.chroma_client import search_rules_semantic


def search_rules(query: str, n_results: int = 5) -> dict:
    """Search the RESL rulebook for rules relevant to the given query using semantic search.

    This tool finds rules from the company's rulebook that are most relevant to the
    current question or analysis. Always use this before answering questions to ensure
    compliance with company procedures and standards.

    Args:
        query: A natural language description of what rules to search for.
            Examples: "discharge temperature limits", "safety procedures for assembly",
            "cathode material specifications".
        n_results: Number of top matching rules to return (default 5).

    Returns:
        dict with 'status' and 'rules' list containing matched rules with
        id, title, content, category, and relevance_score.
    """
    try:
        results = search_rules_semantic(query, n_results=n_results)

        if not results:
            return {
                "status": "success",
                "rules_found": 0,
                "rules": [],
                "message": "No matching rules found in the rulebook.",
            }

        rules = []
        for r in results:
            metadata = r.get("metadata", {})
            rules.append({
                "id": r["id"],
                "title": metadata.get("title", ""),
                "content": r["document"],
                "category": metadata.get("category", ""),
                "relevance_score": round(1 - (r.get("distance", 1)), 3) if r.get("distance") is not None else None,
            })

        return {
            "status": "success",
            "rules_found": len(rules),
            "rules": rules,
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to search rulebook: {str(e)}",
        }


def get_rules_by_category(category: str) -> dict:
    """Get all rules from the rulebook in a specific category.

    Args:
        category: The rule category to filter by (e.g. "safety", "design",
            "assembly", "testing", "quality").

    Returns:
        dict with 'status' and 'rules' list.
    """
    try:
        results = search_rules_semantic(
            query=f"rules in category {category}",
            n_results=20,
            category=category,
        )

        rules = []
        for r in results:
            metadata = r.get("metadata", {})
            rules.append({
                "id": r["id"],
                "title": metadata.get("title", ""),
                "content": r["document"],
                "category": metadata.get("category", ""),
            })

        return {
            "status": "success",
            "rules_found": len(rules),
            "rules": rules,
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Failed to get rules by category: {str(e)}",
        }
