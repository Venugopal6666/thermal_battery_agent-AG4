"""Rulebook router — CRUD endpoints for managing rules with ChromaDB sync."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services import rulebook_service

router = APIRouter(prefix="/api/rules", tags=["rulebook"])


# ── Schemas ─────────────────────────────────────────────────


class RuleCreate(BaseModel):
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[list[str]] = None


class RuleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None


class RuleResponse(BaseModel):
    id: str
    title: str
    content: str
    category: Optional[str]
    tags: list[str]
    source: str
    source_file: Optional[str]
    is_active: bool
    created_at: str
    updated_at: str


class RuleSearchResult(BaseModel):
    id: str
    title: str
    content: str
    category: Optional[str]
    relevance_score: Optional[float]


def _rule_to_response(rule) -> RuleResponse:
    return RuleResponse(
        id=str(rule.id),
        title=rule.title,
        content=rule.content,
        category=rule.category,
        tags=rule.tags or [],
        source=rule.source,
        source_file=rule.source_file,
        is_active=rule.is_active,
        created_at=rule.created_at.isoformat(),
        updated_at=rule.updated_at.isoformat(),
    )


# ── Endpoints ───────────────────────────────────────────────


@router.get("/", response_model=list[RuleResponse])
async def list_rules(
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List all rules with optional filters."""
    rules = rulebook_service.list_rules(
        db, category=category, is_active=is_active, search=search, skip=skip, limit=limit
    )
    return [_rule_to_response(r) for r in rules]


@router.post("/", response_model=RuleResponse, status_code=201)
async def create_rule(rule_data: RuleCreate, db: Session = Depends(get_db)):
    """Add a new rule manually. Syncs to ChromaDB automatically."""
    rule = rulebook_service.create_rule(
        db,
        title=rule_data.title,
        content=rule_data.content,
        category=rule_data.category,
        tags=rule_data.tags,
    )
    return _rule_to_response(rule)


@router.get("/categories", response_model=list[str])
async def get_categories(db: Session = Depends(get_db)):
    """Get a list of all rule categories."""
    return rulebook_service.get_rule_categories(db)


@router.get("/search", response_model=list[RuleSearchResult])
async def search_rules_semantic(
    q: str = Query(..., min_length=1),
    n_results: int = Query(5, ge=1, le=20),
    category: Optional[str] = None,
):
    """Semantic search through rules via ChromaDB."""
    results = rulebook_service.search_rules(q, n_results=n_results, category=category)
    return [
        RuleSearchResult(
            id=r["id"],
            title=r.get("metadata", {}).get("title", ""),
            content=r.get("document", ""),
            category=r.get("metadata", {}).get("category"),
            relevance_score=round(1 - r.get("distance", 1), 3) if r.get("distance") is not None else None,
        )
        for r in results
    ]


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(rule_id: str, db: Session = Depends(get_db)):
    """Get a single rule by ID."""
    rule = rulebook_service.get_rule_by_id(db, uuid.UUID(rule_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _rule_to_response(rule)


@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: str, rule_data: RuleUpdate, db: Session = Depends(get_db)):
    """Update a rule. Automatically re-syncs to ChromaDB."""
    rule = rulebook_service.update_rule(
        db,
        rule_id=uuid.UUID(rule_id),
        title=rule_data.title,
        content=rule_data.content,
        category=rule_data.category,
        tags=rule_data.tags,
        is_active=rule_data.is_active,
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _rule_to_response(rule)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, db: Session = Depends(get_db)):
    """Delete a rule from PostgreSQL and ChromaDB."""
    success = rulebook_service.delete_rule(db, uuid.UUID(rule_id))
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted"}


@router.post("/upload", response_model=list[RuleResponse])
async def upload_document(
    file: UploadFile = File(...),
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Upload a PDF or DOCX document to extract rules.

    The document is parsed and individual rules/sections are extracted
    and added to the rulebook.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files are supported")

    content = await file.read()

    # Parse the document
    extracted_rules = _parse_document(content, ext, category)

    if not extracted_rules:
        raise HTTPException(status_code=400, detail="No rules could be extracted from the document")

    # Bulk create
    rules = rulebook_service.bulk_create_rules(
        db,
        rules_data=extracted_rules,
        source="uploaded_document",
        source_file=file.filename,
    )

    return [_rule_to_response(r) for r in rules]


@router.post("/rebuild-vectors")
async def rebuild_vectors(db: Session = Depends(get_db)):
    """Admin: full re-sync of all active rules from PostgreSQL to ChromaDB."""
    count = rulebook_service.rebuild_vector_store(db)
    return {"status": "success", "rules_synced": count}


# ── Document Parsing ────────────────────────────────────────


def _parse_document(content: bytes, ext: str, category: str | None = None) -> list[dict]:
    """Parse a document and extract rules/sections."""
    text = ""

    if ext == "txt":
        text = content.decode("utf-8", errors="ignore")
    elif ext == "pdf":
        try:
            import PyPDF2
            import io
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")
    elif ext == "docx":
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(content))
            for para in doc.paragraphs:
                text += para.text + "\n"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse DOCX: {str(e)}")

    if not text.strip():
        return []

    # Split into sections/rules by numbered patterns or headers
    rules = _split_into_rules(text, category)
    return rules


def _split_into_rules(text: str, category: str | None = None) -> list[dict]:
    """Split document text into individual rule entries.

    Uses heuristics: splits by numbered items, headings, or double newlines.
    """
    import re

    rules = []

    # Try splitting by numbered items (e.g., "1.", "2.", "1)", "Rule 1:")
    numbered_pattern = r'(?:^|\n)(?:\d+[\.\)]\s|Rule\s+\d+[:\s])'
    sections = re.split(numbered_pattern, text)

    if len(sections) > 1:
        # We found numbered sections
        for i, section in enumerate(sections):
            section = section.strip()
            if len(section) < 10:
                continue  # Skip very short fragments

            # Extract title from first line
            lines = section.split("\n", 1)
            title = lines[0].strip()[:200]
            content = section

            rules.append({
                "title": title if title else f"Rule {i + 1}",
                "content": content,
                "category": category,
                "tags": [],
            })
    else:
        # Fall back to splitting by double newlines or paragraphs
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip() and len(p.strip()) > 20]

        if paragraphs:
            for i, para in enumerate(paragraphs):
                lines = para.split("\n", 1)
                title = lines[0].strip()[:200]

                rules.append({
                    "title": title if title else f"Section {i + 1}",
                    "content": para,
                    "category": category,
                    "tags": [],
                })
        else:
            # Treat the whole document as a single rule
            rules.append({
                "title": text.split("\n", 1)[0].strip()[:200] or "Imported Rule",
                "content": text,
                "category": category,
                "tags": [],
            })

    return rules
