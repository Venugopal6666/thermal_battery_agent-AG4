"""Rulebook service — CRUD operations on PostgreSQL with automatic ChromaDB sync."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Rule
from services.chroma_client import (
    delete_rule_embedding,
    rebuild_all_embeddings,
    search_rules_semantic,
    upsert_rule_embedding,
)


# ── CREATE ──────────────────────────────────────────────────


def create_rule(
    db: Session,
    title: str,
    content: str,
    category: str | None = None,
    tags: list[str] | None = None,
    source: str = "manual",
    source_file: str | None = None,
) -> Rule:
    """Create a new rule in PostgreSQL and sync to ChromaDB."""
    rule = Rule(
        title=title,
        content=content,
        category=category,
        tags=tags or [],
        source=source,
        source_file=source_file,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    # Sync to ChromaDB
    upsert_rule_embedding(
        rule_id=str(rule.id),
        title=rule.title,
        content=rule.content,
        category=rule.category,
        tags=rule.tags,
    )

    return rule


def bulk_create_rules(
    db: Session,
    rules_data: list[dict],
    source: str = "uploaded_document",
    source_file: str | None = None,
) -> list[Rule]:
    """Create multiple rules at once (e.g. from document upload)."""
    created_rules = []
    for data in rules_data:
        rule = Rule(
            title=data["title"],
            content=data["content"],
            category=data.get("category"),
            tags=data.get("tags", []),
            source=source,
            source_file=source_file,
        )
        db.add(rule)
        created_rules.append(rule)

    db.commit()
    for rule in created_rules:
        db.refresh(rule)
        upsert_rule_embedding(
            rule_id=str(rule.id),
            title=rule.title,
            content=rule.content,
            category=rule.category,
            tags=rule.tags,
        )

    return created_rules


# ── READ ────────────────────────────────────────────────────


def get_rule_by_id(db: Session, rule_id: uuid.UUID) -> Rule | None:
    """Get a single rule by its ID."""
    return db.query(Rule).filter(Rule.id == rule_id).first()


def list_rules(
    db: Session,
    category: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Rule]:
    """List rules with optional filters."""
    query = db.query(Rule)

    if category:
        query = query.filter(Rule.category == category)
    if is_active is not None:
        query = query.filter(Rule.is_active == is_active)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (Rule.title.ilike(search_pattern)) | (Rule.content.ilike(search_pattern))
        )

    return query.order_by(Rule.created_at.desc()).offset(skip).limit(limit).all()


def get_rule_categories(db: Session) -> list[str]:
    """Get a list of distinct rule categories."""
    results = db.query(Rule.category).distinct().filter(Rule.category.isnot(None)).all()
    return [r[0] for r in results]


def get_rules_by_ids(db: Session, rule_ids: list[str]) -> list[Rule]:
    """Get multiple rules by their IDs."""
    uuids = [uuid.UUID(rid) for rid in rule_ids]
    return db.query(Rule).filter(Rule.id.in_(uuids)).all()


# ── UPDATE ──────────────────────────────────────────────────


def update_rule(
    db: Session,
    rule_id: uuid.UUID,
    title: str | None = None,
    content: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    is_active: bool | None = None,
) -> Rule | None:
    """Update a rule in PostgreSQL and re-sync to ChromaDB."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        return None

    if title is not None:
        rule.title = title
    if content is not None:
        rule.content = content
    if category is not None:
        rule.category = category
    if tags is not None:
        rule.tags = tags
    if is_active is not None:
        rule.is_active = is_active

    rule.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rule)

    # Sync to ChromaDB — if deactivated, remove from vector store
    if rule.is_active:
        upsert_rule_embedding(
            rule_id=str(rule.id),
            title=rule.title,
            content=rule.content,
            category=rule.category,
            tags=rule.tags,
        )
    else:
        delete_rule_embedding(str(rule.id))

    return rule


# ── DELETE ──────────────────────────────────────────────────


def delete_rule(db: Session, rule_id: uuid.UUID) -> bool:
    """Delete a rule from PostgreSQL and ChromaDB."""
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        return False

    # Delete from ChromaDB first
    delete_rule_embedding(str(rule.id))

    # Delete from PostgreSQL
    db.delete(rule)
    db.commit()
    return True


# ── SYNC ────────────────────────────────────────────────────


def rebuild_vector_store(db: Session) -> int:
    """Full re-sync: rebuild all ChromaDB embeddings from PostgreSQL.

    Returns the number of rules synced.
    """
    active_rules = db.query(Rule).filter(Rule.is_active == True).all()
    rules_data = [
        {
            "id": str(r.id),
            "title": r.title,
            "content": r.content,
            "category": r.category,
            "tags": r.tags,
        }
        for r in active_rules
    ]
    rebuild_all_embeddings(rules_data)
    return len(rules_data)


# ── SEARCH (via ChromaDB) ──────────────────────────────────


def search_rules(query: str, n_results: int = 5, category: str | None = None) -> list[dict]:
    """Semantic search through rules via ChromaDB."""
    return search_rules_semantic(query, n_results=n_results, category=category)
