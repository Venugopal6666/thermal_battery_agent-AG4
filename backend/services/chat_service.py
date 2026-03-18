"""Chat service — conversation and message management in PostgreSQL."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Conversation, Message


# ── CONVERSATIONS ───────────────────────────────────────────


def create_conversation(db: Session, title: str = "New Conversation") -> Conversation:
    """Create a new conversation."""
    conversation = Conversation(title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    """Get a conversation with its messages."""
    return db.query(Conversation).filter(Conversation.id == conversation_id).first()


def list_conversations(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    include_archived: bool = False,
) -> list[Conversation]:
    """List conversations, pinned first then newest first."""
    query = db.query(Conversation)
    if not include_archived:
        query = query.filter(Conversation.is_archived == False)
    return (
        query.order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def search_conversations(db: Session, search_query: str, limit: int = 20) -> list[Conversation]:
    """Search conversations by title or message content."""
    pattern = f"%{search_query}%"

    # Search in conversation titles
    by_title = db.query(Conversation).filter(Conversation.title.ilike(pattern)).all()
    title_ids = {c.id for c in by_title}

    # Search in message content
    msg_conv_ids = (
        db.query(Message.conversation_id)
        .filter(Message.content.ilike(pattern))
        .distinct()
        .limit(limit)
        .all()
    )
    msg_ids = {row[0] for row in msg_conv_ids}

    all_ids = title_ids | msg_ids
    if not all_ids:
        return []

    return (
        db.query(Conversation)
        .filter(Conversation.id.in_(all_ids))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )


def delete_conversation(db: Session, conversation_id: uuid.UUID) -> bool:
    """Delete a conversation and all its messages."""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return False
    db.delete(conversation)
    db.commit()
    return True


def update_conversation_title(
    db: Session, conversation_id: uuid.UUID, title: str
) -> Conversation | None:
    """Update a conversation's title."""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return None
    conversation.title = title
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    return conversation


def toggle_pin_conversation(
    db: Session, conversation_id: uuid.UUID
) -> Conversation | None:
    """Toggle pin state of a conversation."""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return None
    conversation.is_pinned = not conversation.is_pinned
    db.commit()
    db.refresh(conversation)
    return conversation


# ── MESSAGES ────────────────────────────────────────────────


def add_message(
    db: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    mode: str = "normal",
    rules_used: list[str] | None = None,
    bq_queries_run: list[str] | None = None,
    thinking_content: str | None = None,
) -> Message:
    """Add a message to a conversation."""
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        mode=mode,
        rules_used=rules_used or [],
        bq_queries_run=bq_queries_run or [],
        thinking_content=thinking_content,
    )
    db.add(message)

    # Update conversation timestamp
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(message)
    return message


def get_conversation_messages(
    db: Session,
    conversation_id: uuid.UUID,
    limit: int = 100,
) -> list[Message]:
    """Get messages for a conversation, ordered chronologically."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .all()
    )


def generate_title_from_message(content: str) -> str:
    """Generate a short conversation title from the first user message."""
    # Take first 80 chars, strip, and truncate at last word boundary
    title = content[:80].strip()
    if len(content) > 80:
        last_space = title.rfind(" ")
        if last_space > 20:
            title = title[:last_space]
        title += "..."
    return title
