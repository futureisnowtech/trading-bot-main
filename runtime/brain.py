"""Single reasoning brain shared by the cockpit orb and the Telegram operator.

There used to be two: dashboard/jarvis_brain.run_jarvis_chat over trading tools, and
notifications/ai_agent.ask_ai over SRE tools. They had separate clients, separate
tool-calling loops, separate model resolution and separate system prompts, so every
capability had to be built twice and the two answered the same question differently.

This module owns the registry, the permission tiers and the one chat loop. Both old
entrypoints remain as thin wrappers, so no call site had to change.

Permission tiers
----------------
Both AI surfaces receive read-tier tools plus ``request_change``, which creates a
governed proposal but cannot execute it.  Mutations are resolved by explicit cockpit
approval outside this model tool loop.  The tier guard remains defense in depth if a
write tool is ever registered in the future.
"""

from __future__ import annotations

import contextvars
import functools
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from google import genai
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

from runtime.reasoning_provider import ReasoningProviderError, ask_with_deepseek
from runtime.reasoning_provider import get_reasoning_provider, model_id_for

READ = "read"
WRITE = "write"

COCKPIT = "cockpit"
TELEGRAM = "telegram"

# No conversational AI surface may directly run a write-tier tool.
_WRITE_SURFACES: set[str] = set()

_CURRENT_SURFACE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "brain_surface", default=COCKPIT
)


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., str]
    tier: str


def _guard(tool: Tool) -> Callable[..., str]:
    """Wrap a write-tier tool so it refuses to run on a read-only surface."""
    if tool.tier == READ:
        return tool.fn

    @functools.wraps(tool.fn)
    def guarded(*args: Any, **kwargs: Any) -> str:
        surface = _CURRENT_SURFACE.get()
        if surface not in _WRITE_SURFACES:
            name = getattr(tool.fn, "__name__", "tool")
            logger.warning("Refused write-tier tool %s on surface %s", name, surface)
            return (
                f"Refused: `{name}` modifies the system and is only available from the "
                f"cockpit, not from {surface}."
            )
        return tool.fn(*args, **kwargs)

    return guarded


def _build_registry() -> dict[str, Tool]:
    """Collect tools from both sources.

    Imports happen here rather than at module scope: jarvis_brain and ai_agent both
    import this module for their wrappers, so top-level imports would be circular.
    """
    from dashboard import jarvis_brain as jb
    from notifications import agent_tools as at

    registry: dict[str, Tool] = {}

    def add(fn: Callable[..., str], tier: str) -> None:
        name = fn.__name__
        if name in registry:
            logger.debug("Tool %s already registered; keeping first definition", name)
            return
        registry[name] = Tool(fn=fn, tier=tier)

    # Trading/analysis tools (formerly cockpit-only).
    add(jb.get_account_status, READ)
    add(jb.get_open_positions, READ)
    add(jb.get_recent_trades, READ)
    add(jb.search_trades_and_positions, READ)
    add(jb.get_trade_post_mortem, READ)
    add(jb.get_ticker_analysis, READ)
    add(jb.get_latest_bot_logs, READ)
    add(jb.get_fee_drag, READ)
    add(jb.get_performance_attribution, READ)
    add(jb.get_system_parameters, READ)
    add(jb.get_operator_brief, READ)
    add(jb.get_entry_funnel, READ)
    add(jb.get_trading_readiness_summary, READ)
    add(jb.recall_brain_history, READ)
    add(jb.request_change, READ)
    add(jb.list_pending_approvals, READ)
    add(jb.show_panel, READ)
    add(jb.get_rbi2_status_summary, READ)
    add(jb.get_cerebro_brief, READ)
    add(jb.propose_cerebro_experiment, READ)
    add(jb.list_cerebro_experiments, READ)

    # SRE/runtime tools (formerly Telegram-only).
    add(at.get_live_kalshi_status, READ)
    add(at.get_recent_veto_summary, READ)
    add(at.get_recent_execution_summary, READ)
    add(at.get_weather_learning_status, READ)
    add(at.get_production_policy_status, READ)
    add(at.get_release_status, READ)
    add(at.run_kalshi_diagnostic, READ)
    add(at.run_storage_audit, READ)
    add(at.read_file, READ)
    add(at.list_files, READ)
    # execute_sql enforces SELECT/WITH/PRAGMA and sets PRAGMA query_only, so it cannot
    # mutate; it is genuinely read-tier despite the scary name.
    add(at.execute_sql, READ)
    return registry


_REGISTRY: dict[str, Tool] | None = None


def get_registry() -> dict[str, Tool]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def tools_for(surface: str) -> list[Callable[..., str]]:
    """Callables a surface may use, write-tier tools already wrapped in their guard."""
    allow_write = surface in _WRITE_SURFACES
    return [
        _guard(tool)
        for tool in get_registry().values()
        if allow_write or tool.tier == READ
    ]


def _gemini_model_id() -> str:
    """Bare Gemini model name, independent of which provider is configured."""
    model = model_id_for("gemini")
    return model[len("models/"):] if model.startswith("models/") else model


def _system_instruction(surface: str) -> str:
    from notifications.ai_agent import get_repo_context

    try:
        context = get_repo_context()
    except Exception as exc:
        logger.warning("Repo context unavailable: %s", exc)
        context = "(repo context unavailable)"

    scope = (
        "You have READ-ONLY diagnostic access. Direct code, shell, release, and parameter "
        "mutation tools are unavailable. If asked to change something, use request_change "
        "to queue a governed proposal for explicit cockpit approval."
    )

    return (
        "You are J.A.R.V.I.S., the operator intelligence for a live Kalshi weather "
        "trading system. You are addressing the system's owner.\n\n"
        "### MANDATE ###\n"
        "1. ACTION FIRST: call the appropriate tool in your first turn rather than "
        "describing what you would do. Prefer broker-first live-truth tools over SQL "
        "when asked about live state.\n"
        "2. EMPIRICAL PROOF: base every claim on tool output. Never speculate.\n"
        "3. MULTI-STEP: chain tools when needed (list files -> read file -> analyze).\n"
        "4. PRECISION: be concise and technically exact. Real money is at stake.\n"
        "5. NO HALLUCINATIONS: if a tool returns nothing, say so plainly.\n"
        "6. TRUTH BUCKETS: separate verified facts, inferred causes, and unverified items.\n"
        "7. DO NOT COLLAPSE DISTINCT FAILURE MODES: vetoes, execution blocks, and "
        "post-submit depth failures are separate categories.\n"
        "8. PRODUCTION INVARIANTS: current entries are taker-only IOC; the probability "
        "path is deterministic GFS/ECMWF plus actual AIGFS disagreement, optional HRRR, "
        "and bounded physics; there is no commercial Open-Meteo ensemble. RBI 2.0 may "
        "collect official evidence during its gate, but learned weights are not active "
        "until at least seven days and 24 independent events pass and a human promotes "
        "the challenger. Use get_production_policy_status for exact current values.\n\n"
        f"### ACCESS SCOPE ###\n{scope}\n\n"
        f"### CONTEXTUAL TRUTH ###\n{context}"
    )


def _remember(surface: str, question: str, answer: str) -> None:
    """Persist a Q&A turn so a future session can recall it.

    Chat history today is only session_state (cockpit) or nothing (Telegram builds
    a fresh single-message context every call) -- so "what did we decide about the
    Kelly fraction last week" has never had an answer. Reuses system_events, the
    table every other runtime log already writes to, rather than adding a new one;
    recall_brain_history filters on the source prefix this uses.
    """
    try:
        from logging_db.trade_logger import log_event

        log_event("INFO", f"brain:{surface}", f"Q: {question}\nA: {answer}"[:4000])
    except Exception as exc:
        logger.warning("Could not persist brain history: %s", exc)


def ask(messages: list[dict], *, surface: str = COCKPIT) -> str:
    """Answer a conversation using the tools the surface is allowed to use.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    """
    if not messages:
        return "No question provided."

    token = _CURRENT_SURFACE.set(surface)
    try:
        provider = get_reasoning_provider()
        tools = tools_for(surface)
        degraded_notice = ""
        if provider == "deepseek":
            try:
                return ask_with_deepseek(
                    messages,
                    system_instruction=_system_instruction(surface),
                    tools=tools,
                    temperature=0.3,
                )
            except ReasoningProviderError as exc:
                # An exhausted balance, a rate limit or an outage should degrade the
                # operator surfaces, not silence them. ask_with_deepseek only raises
                # this before any tool has run, so re-asking Gemini cannot repeat a
                # tool call that already took effect.
                logger.warning("DeepSeek unavailable (%s); falling back to Gemini.", exc)
                degraded_notice = f"⚠️ DeepSeek unavailable ({exc}) — answered by the Gemini fallback.\n\n"

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return f"{degraded_notice}⚠️ Brain is inactive: GOOGLE_API_KEY is not set."
        if not HAS_GENAI_SDK:
            return f"{degraded_notice}⚠️ Brain is inactive: google-genai SDK not installed."

        client = genai.Client(api_key=api_key)
        config = {
            "system_instruction": _system_instruction(surface),
            "tools": tools,
            # Let the SDK drive the tool loop so multi-step chains work. The previous
            # cockpit loop ran exactly one round of tool calls and then stopped.
            "automatic_function_calling": {"disable": False},
            "temperature": 0.3,
        }

        history = []
        for msg in messages[:-1]:
            role = "user" if msg.get("role") == "user" else "model"
            history.append({"role": role, "parts": [{"text": str(msg.get("content") or "")}]})

        # Always the Gemini id here: on the fallback path the configured provider
        # is still deepseek, so resolving the *configured* model would hand the
        # Gemini client a DeepSeek model name.
        chat = client.chats.create(model=_gemini_model_id(), config=config, history=history)
        response = chat.send_message(str(messages[-1].get("content") or ""))

        text = getattr(response, "text", None)
        if not text:
            return f"{degraded_notice}No textual response was produced. Check logs for tool execution status."
        _remember(surface, str(messages[-1].get("content") or ""), text)
        return f"{degraded_notice}{text}"
    except Exception as exc:
        logger.exception("Brain execution error on surface %s", surface)
        return f"⚠️ Error executing query: {exc}"
    finally:
        _CURRENT_SURFACE.reset(token)
