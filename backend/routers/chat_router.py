"""Chat router — endpoints for sending messages and managing conversations."""

import json
import uuid
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services import chat_service
from services import rulebook_service

# ADK imports for running the agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.agent import root_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ── ADK Runner setup ────────────────────────────────────────

session_service = InMemorySessionService()

runner = Runner(
    agent=root_agent,
    app_name="resl_thermal_battery_agent",
    session_service=session_service,
)

# Track ADK session IDs per conversation
_conversation_sessions: dict[str, str] = {}

# Human-readable descriptions for tool calls
TOOL_DESCRIPTIONS = {
    "query_bigquery": "🔍 Querying BigQuery database...",
    "get_battery_list": "🔋 Fetching battery list...",
    "get_builds_for_battery": "📦 Fetching builds for battery...",
    "get_customer_specs": "📋 Fetching customer specifications...",
    "get_design_parameters": "⚙️ Fetching design parameters...",
    "get_discharge_data": "⚡ Fetching discharge data...",
    "get_temperature_data": "🌡️ Fetching temperature data...",
    "compare_builds": "🔄 Comparing builds...",
    "get_discharge_summary": "📊 Fetching discharge summary...",
    # Discharge analysis tools (server-side computation)
    "calculate_discharge_duration": "⏱️ Computing discharge duration (server-side, all data points)...",
    "calculate_activation_time": "⚡ Computing activation time (server-side)...",
    "calculate_open_circuit_voltage": "🔌 Computing open circuit voltage (server-side)...",
    "calculate_on_load_voltage": "📈 Computing on-load voltage (server-side)...",
    "analyze_build_complete": "🔬 Running complete build analysis (all rulebook metrics, server-side)...",
    "compare_builds_performance": "📊 Comparing builds performance (server-side)...",
    # Calculation tools
    "analyze_discharge_curve": "📈 Analyzing discharge curve...",
    "analyze_temperature_profile": "🌡️ Analyzing temperature profile...",
    "calculate_specific_energy": "⚡ Calculating specific energy...",
    "calculate_energy_density": "🔋 Calculating energy density...",
    "calculate_c_rate": "📐 Calculating C-rate...",
    "calculate_thermal_efficiency": "🔥 Calculating thermal efficiency...",
    "calculate_internal_resistance": "Ω Calculating internal resistance...",
    # Generic computation tools
    "run_aggregation_query": "🧮 Running server-side aggregation (all data points)...",
    "compute_capacity_at_voltage": "⚡ Computing Ampere-seconds capacity at cut-off voltage...",
    "calculate_active_material": "🧪 Computing active material (LiSi/FeS2) per Rules 4.3 & 4.4...",
    "calculate_active_material_utilization": "📊 Computing active material utilization (Table-5) per Rules 4.6 & 4.7...",
    "search_rules": "📖 Searching rulebook...",
    "get_rules_by_category": "📂 Fetching rules by category...",
    "transfer_to_agent": "🤖 Delegating to sub-agent...",
}

# Human-readable descriptions for agent names
AGENT_DESCRIPTIONS = {
    "bigquery_data_agent": "📊 BigQuery Data Agent",
    "analysis_agent": "🔬 Analysis Agent",
    "rules_agent": "📖 Rules Agent",
    "deep_research_agent": "🔎 Deep Research Agent",
    "resl_thermal_battery_agent": "🔋 Root Agent",
}


def _is_code_dump(text: str) -> bool:
    """Detect if text is a raw code dump (tool definitions leaked by the LLM).
    Returns True if the text looks like Python function/class definitions rather
    than a genuine response to the user."""
    stripped = text.strip()
    # Check for Python function/class definition patterns
    code_indicators = [
        'def transfer_to_agent(',
        'def run_aggregation_query(',
        'def compute_capacity_at_voltage(',
        'def query_bigquery(',
        'def analyze_build_complete(',
        'def calculate_discharge_duration(',
        'def calculate_activation_time(',
        'def calculate_open_circuit_voltage(',
        'def calculate_on_load_voltage(',
        'def compare_builds_performance(',
        'def get_battery_list(',
        'def get_builds_for_battery(',
        'def get_customer_specs(',
        'def get_design_parameters(',
        'def get_discharge_data(',
        'def get_temperature_data(',
        'def compare_builds(',
        'def get_discharge_summary(',
    ]
    # If the text contains multiple function definitions, it's a code dump
    matches = sum(1 for indicator in code_indicators if indicator in stripped)
    if matches >= 2:
        return True
    # If it starts with 'def ' or 'class ' and looks like raw code
    lines = stripped.split('\n')
    if len(lines) > 3:
        def_count = sum(1 for line in lines if line.strip().startswith('def ') or line.strip().startswith('class '))
        if def_count >= 2:
            return True
    return False


# ── Schemas ─────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    mode: str = "normal"  # 'normal' | 'deep_think' | 'deep_research'


class SendMessageResponse(BaseModel):
    conversation_id: str
    message_id: str
    response: str
    rules_used: list[str]
    bq_queries_run: list[str]
    thinking_content: Optional[str] = None
    thinking_steps: list[str] = []
    mode: str


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    mode: str
    rules_used: list[str]
    bq_queries_run: list[str]
    thinking_content: Optional[str] = None
    created_at: str


# ── Helpers ─────────────────────────────────────────────────

def _build_mode_instruction(mode: str) -> str:
    """Get additional instruction based on the selected mode."""
    if mode == "deep_think":
        return (
            "\n\n[MODE: DEEP THINK] Take your time to reason through this step by step. "
            "Show your detailed reasoning process. Consider multiple angles and "
            "possibilities before reaching your conclusion."
        )
    elif mode == "deep_research":
        return (
            "\n\n[MODE: DEEP RESEARCH] Conduct a thorough, multi-step investigation. "
            "Query multiple data sources, compare across builds, check all relevant rules, "
            "and produce a structured research report with executive summary, findings, "
            "data tables, and recommendations."
        )
    return ""


def _load_all_rules() -> tuple[list[dict], str]:
    """Load ALL active rules from the database and format them for injection.

    Instead of similarity search (which misses rules), we load every active rule
    and let the LLM decide which ones are relevant to the user's query.
    gemini-2.5-flash has a 1M token context window, so this is safe.

    Returns:
        (all_rules, context_block)
        - all_rules: list of {title, content, category} dicts
        - context_block: formatted string to inject into the agent's prompt
    """
    try:
        from database import SessionLocal
        from models import Rule

        db = SessionLocal()
        try:
            active_rules = (
                db.query(Rule)
                .filter(Rule.is_active == True)
                .order_by(Rule.category, Rule.title)
                .all()
            )

            if not active_rules:
                return [], ""

            all_rules = []
            rules_text_parts = []
            current_category = None

            for rule in active_rules:
                title = rule.title or "Untitled Rule"
                content = rule.content or ""
                category = rule.category or "General"

                all_rules.append({
                    "title": title,
                    "content": content,
                    "category": category,
                })

                # Group by category for clarity
                if category != current_category:
                    current_category = category
                    rules_text_parts.append(f"\n=== Category: {category} ===")

                rules_text_parts.append(
                    f"\nRule: {title}\n{content}"
                )

            context_block = (
                "\n\n[COMPLETE COMPANY RULEBOOK — ALL RULES]\n"
                "INSTRUCTIONS:\n"
                "1. Read ALL rules below carefully.\n"
                "2. Identify which rules are relevant to the user's query.\n"
                "3. Apply ALL relevant rules to the data you retrieve.\n"
                "4. For each relevant rule, create a Rule Compliance section:\n"
                "   | Rule | Requirement | Actual Value | Status (PASS/FAIL) |\n"
                "5. If a rule is violated, highlight it as a WARNING.\n"
                "6. If no rules are relevant, state 'No applicable rules found.'\n\n"
                + "\n---".join(rules_text_parts)
                + "\n\n[END OF RULEBOOK]\n\n"
                "[DATA ACCURACY REMINDER]\n"
                "Every number in your response MUST come from a tool response.\n"
                "NEVER invent, estimate, or round values. Copy them exactly.\n"
                "If data is unavailable, state 'Data not available'.\n"
                "[END REMINDER]\n"
            )

            return all_rules, context_block

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Rule loading failed: {e}")
        return [], ""


def _get_tool_description(fn_name: str, args: dict = None) -> str:
    """Get a human-readable description for a tool call."""
    base = TOOL_DESCRIPTIONS.get(fn_name, f"🔧 Calling {fn_name}...")

    # Add context-specific details
    if args:
        if fn_name in ("get_customer_specs", "get_builds_for_battery") and "battery_code" in args:
            base = base.rstrip("...") + f" (Battery {args['battery_code']})..."
        elif fn_name in ("get_design_parameters", "get_discharge_data", "get_temperature_data",
                         "calculate_discharge_duration", "calculate_activation_time",
                         "calculate_open_circuit_voltage", "calculate_on_load_voltage",
                         "analyze_build_complete"):
            parts = []
            if "battery_code" in args:
                parts.append(f"Battery {args['battery_code']}")
            if "build_number" in args:
                parts.append(f"Build {args['build_number']}")
            if "discharge_temperature" in args and args["discharge_temperature"]:
                parts.append(f"Temp {args['discharge_temperature']}")
            if parts:
                base = base.rstrip("...") + f" ({', '.join(parts)})..."
        elif fn_name == "compare_builds_performance":
            bc = args.get("battery_code", "?")
            builds = args.get("build_numbers", [])
            if builds:
                base = f"📊 Comparing {len(builds)} builds for Battery {bc} (server-side)..."
        elif fn_name == "query_bigquery" and "sql_query" in args:
            sql = args["sql_query"][:80].replace("\n", " ").strip()
            base = f"🔍 Running SQL: {sql}..."
        elif fn_name == "search_rules" and "query" in args:
            base = f"📖 Searching rules for: \"{args['query'][:50]}\"..."

    return base


async def _run_agent_once(conversation_id: str, user_message: str, mode: str) -> dict:
    """Run the ADK agent once and return the response with metadata."""
    # Get or create ADK session for this conversation
    session_id = _conversation_sessions.get(conversation_id)
    if not session_id:
        session_id = str(uuid.uuid4())
        session = await session_service.create_session(
            app_name="resl_thermal_battery_agent",
            user_id="resl_user",
            session_id=session_id,
        )
        _conversation_sessions[conversation_id] = session_id

    # Load ALL rules — skip only for simple greetings
    greetings = {'hi', 'hello', 'hey', 'thanks', 'thank you', 'bye', 'ok', 'okay'}
    if user_message.strip().lower() in greetings:
        all_rules, rules_context = [], ""
    else:
        all_rules, rules_context = _load_all_rules()
    pre_rules_used = [r["title"] for r in all_rules]

    # Add mode instruction and rules to the message
    mode_instruction = _build_mode_instruction(mode)
    full_message = user_message + mode_instruction + rules_context

    # Create the user content
    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=full_message)],
    )

    # Run the agent
    response_text = ""
    rules_used = list(pre_rules_used)  # Start with auto-found rules
    bq_queries_run = []
    thinking_content = None
    thinking_steps = []

    async for event in runner.run_async(
        user_id="resl_user",
        session_id=session_id,
        new_message=user_content,
    ):
        # Collect the final response text
        if event.content and event.content.parts:
            for part in event.content.parts:
                # Track thinking content separately for the UI thinking panel
                is_thought = hasattr(part, 'thought') and part.thought
                if is_thought and part.text:
                    thinking_content = (thinking_content or "") + part.text
                
                # Collect response text from ALL non-thought text parts
                # (Gemini 2.5 Flash final answer comes as non-thought text)
                if part.text and not is_thought:
                    text = part.text.strip()
                    is_code = _is_code_dump(text) if text else False
                    if is_code:
                        logger.warning(f"[FILTERED CODE DUMP] len={len(part.text)} preview={repr(part.text[:100])}")
                    elif text:
                        response_text += part.text
                        logger.debug(f"[RESPONSE] len={len(part.text)} preview={repr(part.text[:80])}")

        # Track function calls for metadata and thinking steps
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    fn_name = part.function_call.name
                    args = part.function_call.args or {}

                    # Track BQ queries
                    if fn_name == "query_bigquery" and "sql_query" in args:
                        bq_queries_run.append(args["sql_query"])

                    # Add thinking step
                    step_desc = _get_tool_description(fn_name, args)
                    if step_desc not in thinking_steps:
                        thinking_steps.append(step_desc)

                    # Track agent transfers
                    if fn_name == "transfer_to_agent":
                        agent_name = args.get("agent_name", "unknown")
                        desc = AGENT_DESCRIPTIONS.get(agent_name, f"🤖 {agent_name}")
                        step = f"Delegating to {desc}..."
                        if step not in thinking_steps:
                            thinking_steps.append(step)

                if hasattr(part, 'function_response') and part.function_response:
                    fn_name = part.function_response.name
                    if fn_name == "search_rules":
                        resp = part.function_response.response
                        if isinstance(resp, dict) and resp.get("rules"):
                            for rule in resp["rules"]:
                                rule_label = rule.get("title", rule.get("id", "Unknown"))
                                if rule_label not in rules_used:
                                    rules_used.append(rule_label)

                    # Add completion step
                    complete_step = f"✅ Got results from {fn_name}"
                    if complete_step not in thinking_steps:
                        thinking_steps.append(complete_step)

    return {
        "response": response_text,
        "rules_used": rules_used,
        "bq_queries_run": bq_queries_run,
        "thinking_content": thinking_content,
        "thinking_steps": thinking_steps,
    }


async def _run_agent(conversation_id: str, user_message: str, mode: str) -> dict:
    """Run the ADK agent with retry logic for rate-limit (429) errors."""
    max_retries = 3
    base_delay = 5  # seconds

    for attempt in range(max_retries + 1):
        try:
            return await _run_agent_once(conversation_id, user_message, mode)
        except Exception as e:
            error_str = str(e)
            is_rate_limited = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

            if is_rate_limited and attempt < max_retries:
                delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
                logger.warning(
                    f"Rate limited (attempt {attempt + 1}/{max_retries + 1}). "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
                # Create a new session for retry (old session might be in bad state)
                new_session_id = str(uuid.uuid4())
                await session_service.create_session(
                    app_name="resl_thermal_battery_agent",
                    user_id="resl_user",
                    session_id=new_session_id,
                )
                _conversation_sessions[conversation_id] = new_session_id
            else:
                raise


async def _run_agent_streaming(conversation_id: str, user_message: str, mode: str):
    """Run the ADK agent and yield SSE events for real-time thinking display.
    Includes retry logic for 429 rate limit errors.
    """
    max_retries = 3
    base_delay = 5  # seconds

    for attempt in range(max_retries + 1):
        try:
            # Get or create ADK session for this conversation
            session_id = _conversation_sessions.get(conversation_id)
            if not session_id:
                session_id = str(uuid.uuid4())
                session = await session_service.create_session(
                    app_name="resl_thermal_battery_agent",
                    user_id="resl_user",
                    session_id=session_id,
                )
                _conversation_sessions[conversation_id] = session_id

            # Load ALL rules — skip only for simple greetings
            greetings = {'hi', 'hello', 'hey', 'thanks', 'thank you', 'bye', 'ok', 'okay'}
            if user_message.strip().lower() in greetings:
                all_rules, rules_context = [], ""
            else:
                all_rules, rules_context = _load_all_rules()
            pre_rules_used = [r["title"] for r in all_rules]

            # Add mode instruction and rules to the message
            mode_instruction = _build_mode_instruction(mode)
            full_message = user_message + mode_instruction + rules_context

            user_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=full_message)],
            )

            response_text = ""
            rules_used = list(pre_rules_used)  # Start with auto-found rules
            bq_queries_run = []
            thinking_content = None
            seen_steps = set()

            # Send initial thinking event
            yield f"data: {json.dumps({'type': 'thinking', 'step': 'Processing your request...'})}\n\n"

            # Send rule search event if rules were found
            if all_rules:
                rule_names = ', '.join(r['title'][:40] for r in all_rules[:3])
                more_text = f' (+{len(all_rules) - 3} more)' if len(all_rules) > 3 else ''
                rule_step = f'Loaded {len(all_rules)} rulebook rule(s): {rule_names}{more_text}'
                yield f"data: {json.dumps({'type': 'thinking', 'step': rule_step})}\n\n"
                seen_steps.add(rule_step)

            async for event in runner.run_async(
                user_id="resl_user",
                session_id=session_id,
                new_message=user_content,
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        # Track thinking content separately
                        is_thought = hasattr(part, 'thought') and part.thought
                        if is_thought and part.text:
                            thinking_content = (thinking_content or "") + part.text
                        
                        # Collect response text from ALL non-thought text parts
                        if part.text and not is_thought:
                            text = part.text.strip()
                            is_code = _is_code_dump(text) if text else False
                            if is_code:
                                logger.warning(f"[STREAM FILTERED] code dump len={len(part.text)}")
                            elif text:
                                response_text += part.text

                        # Stream function calls as thinking steps
                        if hasattr(part, 'function_call') and part.function_call:
                            fn_name = part.function_call.name
                            args = part.function_call.args or {}

                            if fn_name == "query_bigquery" and "sql_query" in args:
                                bq_queries_run.append(args["sql_query"])

                            # Send thinking step event
                            step_desc = _get_tool_description(fn_name, args)
                            if step_desc not in seen_steps:
                                seen_steps.add(step_desc)
                                yield f"data: {json.dumps({'type': 'thinking', 'step': step_desc})}\n\n"

                            # Track agent transfers
                            if fn_name == "transfer_to_agent":
                                agent_name = args.get("agent_name", "unknown")
                                desc = AGENT_DESCRIPTIONS.get(agent_name, f"🤖 {agent_name}")
                                step = f"Delegating to {desc}..."
                                if step not in seen_steps:
                                    seen_steps.add(step)
                                    yield f"data: {json.dumps({'type': 'thinking', 'step': step})}\n\n"

                        if hasattr(part, 'function_response') and part.function_response:
                            fn_name = part.function_response.name
                            if fn_name == "search_rules":
                                resp = part.function_response.response
                                if isinstance(resp, dict) and resp.get("rules"):
                                    for rule in resp["rules"]:
                                        rule_label = rule.get("title", rule.get("id", "Unknown"))
                                        if rule_label not in rules_used:
                                            rules_used.append(rule_label)

                            # Send completion event
                            complete_step = f"✅ Got results from {fn_name}"
                            if complete_step not in seen_steps:
                                seen_steps.add(complete_step)
                                yield f"data: {json.dumps({'type': 'thinking', 'step': complete_step})}\n\n"

            # Send final response
            yield f"data: {json.dumps({'type': 'done', 'response': response_text, 'rules_used': rules_used, 'bq_queries_run': bq_queries_run, 'thinking_content': thinking_content, 'thinking_steps': list(seen_steps)})}\n\n"
            return  # Success — exit the retry loop

        except Exception as e:
            error_str = str(e)
            is_rate_limited = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

            if is_rate_limited and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Rate limited during streaming (attempt {attempt + 1}/{max_retries + 1}). "
                    f"Retrying in {delay}s..."
                )
                # Notify the frontend about the retry
                yield f"data: {json.dumps({'type': 'thinking', 'step': f'⏳ Rate limited. Retrying in {delay}s...'})}\n\n"
                await asyncio.sleep(delay)
                # Create a new session for retry
                new_session_id = str(uuid.uuid4())
                await session_service.create_session(
                    app_name="resl_thermal_battery_agent",
                    user_id="resl_user",
                    session_id=new_session_id,
                )
                _conversation_sessions[conversation_id] = new_session_id
            else:
                # Final failure — send error as the response
                yield f"data: {json.dumps({'type': 'done', 'response': f'⚠️ Error: {error_str}', 'rules_used': [], 'bq_queries_run': [], 'thinking_content': None, 'thinking_steps': []})}\n\n"
                return


# ── Endpoints ───────────────────────────────────────────────


@router.post("/send", response_model=SendMessageResponse)
async def send_message(request: SendMessageRequest, db: Session = Depends(get_db)):
    """Send a message and get an AI response."""
    # Create or get conversation
    if request.conversation_id:
        conv_id = uuid.UUID(request.conversation_id)
        conversation = chat_service.get_conversation(db, conv_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        # Auto-generate title from first message
        title = chat_service.generate_title_from_message(request.message)
        conversation = chat_service.create_conversation(db, title=title)

    # Save user message
    user_msg = chat_service.add_message(
        db,
        conversation_id=conversation.id,
        role="user",
        content=request.message,
        mode=request.mode,
    )

    # Run the agent
    try:
        agent_result = await _run_agent(
            conversation_id=str(conversation.id),
            user_message=request.message,
            mode=request.mode,
        )
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        agent_result = {
            "response": f"I encountered an error while processing your request: {str(e)}",
            "rules_used": [],
            "bq_queries_run": [],
            "thinking_content": None,
            "thinking_steps": [],
        }

    # Save assistant message
    assistant_msg = chat_service.add_message(
        db,
        conversation_id=conversation.id,
        role="assistant",
        content=agent_result["response"],
        mode=request.mode,
        rules_used=agent_result["rules_used"],
        bq_queries_run=agent_result["bq_queries_run"],
        thinking_content=agent_result.get("thinking_content"),
    )

    return SendMessageResponse(
        conversation_id=str(conversation.id),
        message_id=str(assistant_msg.id),
        response=agent_result["response"],
        rules_used=agent_result["rules_used"],
        bq_queries_run=agent_result["bq_queries_run"],
        thinking_content=agent_result.get("thinking_content"),
        thinking_steps=agent_result.get("thinking_steps", []),
        mode=request.mode,
    )


@router.post("/send-stream")
async def send_message_stream(request: SendMessageRequest, db: Session = Depends(get_db)):
    """Send a message and stream back thinking steps + final response via SSE."""
    # Create or get conversation
    if request.conversation_id:
        conv_id = uuid.UUID(request.conversation_id)
        conversation = chat_service.get_conversation(db, conv_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        title = chat_service.generate_title_from_message(request.message)
        conversation = chat_service.create_conversation(db, title=title)

    # Save user message
    chat_service.add_message(
        db,
        conversation_id=conversation.id,
        role="user",
        content=request.message,
        mode=request.mode,
    )

    conv_id_str = str(conversation.id)

    async def event_generator():
        # Send conversation ID first
        yield f"data: {json.dumps({'type': 'conversation_id', 'conversation_id': conv_id_str})}\n\n"

        # Stream thinking steps and final response
        final_result = None
        async for chunk in _run_agent_streaming(conv_id_str, request.message, request.mode):
            yield chunk
            # Parse the last chunk to get the final result
            if '"type": "done"' in chunk or '"type":"done"' in chunk:
                try:
                    data_str = chunk.replace("data: ", "").strip()
                    final_result = json.loads(data_str)
                except Exception:
                    pass

        # Save assistant message to DB
        if final_result:
            chat_service.add_message(
                db,
                conversation_id=conversation.id,
                role="assistant",
                content=final_result.get("response", ""),
                mode=request.mode,
                rules_used=final_result.get("rules_used", []),
                bq_queries_run=final_result.get("bq_queries_run", []),
                thinking_content=final_result.get("thinking_content"),
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/new", response_model=ConversationResponse)
async def create_conversation(db: Session = Depends(get_db)):
    """Create a new empty conversation."""
    conversation = chat_service.create_conversation(db)
    return ConversationResponse(
        id=str(conversation.id),
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
    )


@router.get("/{conversation_id}", response_model=list[MessageResponse])
async def get_conversation_messages(conversation_id: str, db: Session = Depends(get_db)):
    """Get all messages in a conversation."""
    conv_id = uuid.UUID(conversation_id)
    conversation = chat_service.get_conversation(db, conv_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = chat_service.get_conversation_messages(db, conv_id)
    return [
        MessageResponse(
            id=str(msg.id),
            role=msg.role,
            content=msg.content,
            mode=msg.mode,
            rules_used=msg.rules_used or [],
            bq_queries_run=msg.bq_queries_run or [],
            thinking_content=msg.thinking_content,
            created_at=msg.created_at.isoformat(),
        )
        for msg in messages
    ]


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    """Delete a conversation and all its messages."""
    conv_id = uuid.UUID(conversation_id)
    success = chat_service.delete_conversation(db, conv_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Clean up ADK session mapping
    _conversation_sessions.pop(conversation_id, None)
    return {"status": "deleted"}
