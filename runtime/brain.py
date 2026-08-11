"""Single reasoning brain shared by the cockpit orb and the Telegram operator.

There used to be two: dashboard/jarvis_brain.run_jarvis_chat over trading tools, and
notifications/ai_agent.ask_ai over SRE tools. They had separate clients, separate
tool-calling loops, separate model resolution and separate system prompts, so every
capability had to be built twice and the two answered the same question differently.

This module owns the registry, the permission tiers and the one chat loop. Both old
entrypoints remain as thin wrappers, so no call site had to change.

Permission tiers
----------------
Tools are ``read`` or ``write``. The cockpit gets everything; Telegram gets read-tier
only, so a mistyped phone message cannot patch live trading code. Enforcement is in two
places on purpose: the write tools are omitted from the tool list handed to the model,
*and* each write tool is wrapped in a guard that re-checks the active surface. The
second check is what holds if a model invents a tool name or the SDK's automatic
function calling is pointed at a stale list.
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

READ = "read"
WRITE = "write"

COCKPIT = "cockpit"
TELEGRAM = "telegram"

# Surfaces allowed to run write-tier tools.
_WRITE_SURFACES = {COCKPIT}

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
    add(jb.get_system_parameters, READ)
    add(jb.update_system_parameter, WRITE)
    add(jb.apply_hot_patch_code, WRITE)

    # SRE/runtime tools (formerly Telegram-only).
    add(at.get_live_kalshi_status, READ)
    add(at.get_recent_veto_summary, READ)
    add(at.get_recent_execution_summary, READ)
    add(at.get_weather_learning_status, READ)
    add(at.get_release_status, READ)
    add(at.run_kalshi_diagnostic, READ)
    add(at.run_storage_audit, READ)
    add(at.read_file, READ)
    add(at.list_files, READ)
    # execute_sql enforces SELECT/WITH/PRAGMA and sets PRAGMA query_only, so it cannot
    # mutate; it is genuinely read-tier despite the scary name.
    add(at.execute_sql, READ)
    # run_safe_command allowlists shell, but the allowlist includes bare `sqlite3`,
    # which can mutate. Treated as write-tier.
    add(at.run_safe_command, WRITE)
    add(at.replace_text, WRITE)
    # run_release_audit can --promote, which changes whether live entries are allowed.
    add(at.run_release_audit, WRITE)

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


def _model_id() -> str:
    from config import GEMINI_MODEL

    model = (os.getenv("GEMINI_REASONING_MODEL", "").strip() or GEMINI_MODEL or "gemini-2.5-flash").strip()
    return model[len("models/"):] if model.startswith("models/") else model


def _system_instruction(surface: str) -> str:
    from notifications.ai_agent import get_repo_context

    try:
        context = get_repo_context()
    except Exception as exc:
        logger.warning("Repo context unavailable: %s", exc)
        context = "(repo context unavailable)"

    scope = (
        "You have full read and write access, including code patching and parameter changes."
        if surface in _WRITE_SURFACES
        else "You have READ-ONLY access. Tools that modify the system are unavailable here; "
        "if asked to change something, say it must be done from the cockpit."
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
        "post-submit depth failures are separate categories.\n\n"
        f"### ACCESS SCOPE ###\n{scope}\n\n"
        f"### CONTEXTUAL TRUTH ###\n{context}"
    )


def ask(messages: list[dict], *, surface: str = COCKPIT) -> str:
    """Answer a conversation using the tools the surface is allowed to use.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "⚠️ Brain is inactive: GOOGLE_API_KEY is not set."
    if not HAS_GENAI_SDK:
        return "⚠️ Brain is inactive: google-genai SDK not installed."
    if not messages:
        return "No question provided."

    token = _CURRENT_SURFACE.set(surface)
    try:
        client = genai.Client(api_key=api_key)
        config = {
            "system_instruction": _system_instruction(surface),
            "tools": tools_for(surface),
            # Let the SDK drive the tool loop so multi-step chains work. The previous
            # cockpit loop ran exactly one round of tool calls and then stopped.
            "automatic_function_calling": {"disable": False},
            "temperature": 0.3,
        }

        history = []
        for msg in messages[:-1]:
            role = "user" if msg.get("role") == "user" else "model"
            history.append({"role": role, "parts": [{"text": str(msg.get("content") or "")}]})

        chat = client.chats.create(model=_model_id(), config=config, history=history)
        response = chat.send_message(str(messages[-1].get("content") or ""))

        text = getattr(response, "text", None)
        if not text:
            return "No textual response was produced. Check logs for tool execution status."
        return text
    except Exception as exc:
        logger.exception("Brain execution error on surface %s", surface)
        return f"⚠️ Error executing query: {exc}"
    finally:
        _CURRENT_SURFACE.reset(token)
