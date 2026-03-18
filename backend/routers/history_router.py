"""History router — endpoints for listing, searching, renaming, and pinning chat history."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services import chat_service

router = APIRouter(prefix="/api/history", tags=["history"])


# ── Schemas ─────────────────────────────────────────────────


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    is_pinned: bool = False


class RenameRequest(BaseModel):
    title: str


# ── Endpoints ───────────────────────────────────────────────


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all conversations, pinned first then newest first."""
    conversations = chat_service.list_conversations(db, skip=skip, limit=limit)
    return [
        ConversationSummary(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=len(conv.messages) if conv.messages else 0,
            is_pinned=conv.is_pinned if hasattr(conv, "is_pinned") else False,
        )
        for conv in conversations
    ]


@router.get("/search", response_model=list[ConversationSummary])
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Search conversations by title or message content."""
    conversations = chat_service.search_conversations(db, search_query=q, limit=limit)
    return [
        ConversationSummary(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=len(conv.messages) if conv.messages else 0,
            is_pinned=conv.is_pinned if hasattr(conv, "is_pinned") else False,
        )
        for conv in conversations
    ]


@router.patch("/{conversation_id}/rename")
async def rename_conversation(
    conversation_id: str,
    body: RenameRequest,
    db: Session = Depends(get_db),
):
    """Rename a conversation."""
    conv_id = uuid.UUID(conversation_id)
    conversation = chat_service.update_conversation_title(db, conv_id, body.title)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "status": "renamed",
        "id": str(conversation.id),
        "title": conversation.title,
    }


@router.patch("/{conversation_id}/pin")
async def toggle_pin_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Toggle pin/unpin a conversation."""
    conv_id = uuid.UUID(conversation_id)
    conversation = chat_service.toggle_pin_conversation(db, conv_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "status": "pinned" if conversation.is_pinned else "unpinned",
        "id": str(conversation.id),
        "is_pinned": conversation.is_pinned,
    }
