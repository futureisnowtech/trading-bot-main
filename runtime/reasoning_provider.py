"""Provider-aware reasoning helpers for Gemini and DeepSeek."""

from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Callable, get_args, get_origin, get_type_hints

from config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_THINKING_MODE,
    GEMINI_MODEL,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types

    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

try:
    from openai import OpenAI

    HAS_OPENAI_SDK = True
except ImportError:
    HAS_OPENAI_SDK = False

_SUPPORTED_PROVIDERS = {"gemini", "deepseek"}


class ReasoningProviderError(RuntimeError):
    """The selected provider could not answer -- caller should try the fallback.

    Raised for anything that makes the provider unusable rather than merely
    unhelpful: a missing key, a missing SDK, an exhausted balance, a rate limit,
    a network failure. Callers cascade to the other provider on this; they do
    not cascade on a normal answer they simply dislike.
    """


def get_reasoning_provider() -> str:
    provider = os.getenv("REASONING_PROVIDER", "gemini").strip().lower()
    return provider if provider in _SUPPORTED_PROVIDERS else "gemini"


def get_reasoning_model_id() -> str:
    return model_id_for(get_reasoning_provider())


def get_reasoning_display_name() -> str:
    return f"{get_reasoning_provider()}:{get_reasoning_model_id()}"


def get_deepseek_base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEEPSEEK_BASE_URL or "https://api.deepseek.com"


def get_deepseek_reasoning_effort() -> str:
    """Effort tier sent to DeepSeek. Empty string omits the field entirely."""
    effort = (os.getenv("DEEPSEEK_REASONING_EFFORT", "").strip() or DEEPSEEK_REASONING_EFFORT).strip().lower()
    return effort if effort in {"low", "medium", "high"} else ""


def get_deepseek_thinking_mode() -> str:
    """Thinking toggle sent as extra_body. Empty string omits the field entirely."""
    mode = (os.getenv("DEEPSEEK_THINKING_MODE", "").strip() or DEEPSEEK_THINKING_MODE).strip().lower()
    return mode if mode in {"enabled", "disabled"} else ""


def probe_reasoning_model() -> dict[str, Any]:
    """Handshake the configured provider, falling back to the other one.

    The release audit turns a failed probe into a hard blocker, so without the
    cascade an outage at one vendor -- an exhausted balance, a rate limit --
    would stop a deploy even though the other provider is configured, funded and
    reachable. A fallback success reports ok=True with fallback_used set, so the
    audit passes while the report still shows the degradation.
    """
    provider = get_reasoning_provider()
    payload = {
        "ok": False,
        "provider": provider,
        "model_id": get_reasoning_model_id(),
        "response_preview": "",
        "error": "",
        "fallback_used": False,
        "fallback_provider": "",
    }
    primary = _probe_deepseek(payload) if provider == "deepseek" else _probe_gemini(payload)
    if primary.get("ok"):
        return primary

    fallback = "gemini" if provider == "deepseek" else "deepseek"
    primary_error = str(primary.get("error") or "unknown error")
    secondary: dict[str, Any] = {
        "ok": False,
        "provider": provider,
        "model_id": model_id_for(fallback),
        "response_preview": "",
        "error": "",
        "fallback_used": True,
        "fallback_provider": fallback,
    }
    secondary = _probe_gemini(secondary) if fallback == "gemini" else _probe_deepseek(secondary)
    if secondary.get("ok"):
        logger.warning(
            "Reasoning probe: %s failed (%s); %s fallback answered.",
            provider,
            primary_error,
            fallback,
        )
        secondary["error"] = f"{provider} unavailable ({primary_error}); answered by {fallback}"
        return secondary

    primary["error"] = (
        f"{provider} unavailable ({primary_error}); "
        f"{fallback} fallback also failed ({secondary.get('error') or 'unknown error'})"
    )
    return primary


def model_id_for(provider: str) -> str:
    """Resolve a provider's model id without regard to what is configured."""
    if provider == "deepseek":
        return (os.getenv("DEEPSEEK_MODEL", "").strip() or DEEPSEEK_MODEL or "deepseek-v4-flash").strip()
    model = (os.getenv("GEMINI_REASONING_MODEL", "").strip() or GEMINI_MODEL or "gemini-2.5-flash").strip()
    return model if model.startswith("models/") else f"models/{model}"


def ask_with_deepseek(
    messages: list[dict[str, Any]],
    *,
    system_instruction: str,
    tools: list[Callable[..., str]],
    temperature: float,
    max_rounds: int = 8,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ReasoningProviderError("DEEPSEEK_API_KEY is not set")
    if not HAS_OPENAI_SDK:
        raise ReasoningProviderError("openai SDK not installed")

    client = OpenAI(
        api_key=api_key,
        base_url=get_deepseek_base_url(),
    )
    chat_messages = [{"role": "system", "content": system_instruction}]
    for msg in messages:
        role = str(msg.get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        entry: dict[str, Any] = {"role": role, "content": str(msg.get("content") or "")}
        tool_call_id = str(msg.get("tool_call_id") or "").strip()
        if role == "tool" and tool_call_id:
            entry["tool_call_id"] = tool_call_id
        chat_messages.append(entry)

    tool_map = {tool.__name__: tool for tool in tools}
    tool_defs = [_tool_schema(tool) for tool in tools]
    model_id = get_reasoning_model_id()
    reasoning_effort = get_deepseek_reasoning_effort()
    thinking_mode = get_deepseek_thinking_mode()
    executed_tools = False

    for _ in range(max_rounds):
        request: dict[str, Any] = {
            "model": model_id,
            "messages": chat_messages,
            "temperature": temperature,
        }
        if tool_defs:
            request["tools"] = tool_defs
        if reasoning_effort:
            request["reasoning_effort"] = reasoning_effort
        if thinking_mode:
            request["extra_body"] = {"thinking": {"type": thinking_mode}}

        try:
            response = client.chat.completions.create(**request)
        except Exception as exc:
            # Only offer the caller a fallback while no tool has run yet. Once a
            # tool has executed, retrying the whole conversation on the other
            # provider would re-execute it -- harmless for a read, a duplicated
            # mutation for a write. Fail closed instead.
            if executed_tools:
                logger.exception("DeepSeek failed after executing tools; not cascading")
                return (
                    f"⚠️ DeepSeek failed partway through the request, after tools had already run: {exc}. "
                    "Not retrying on the fallback provider, because that would repeat those tool calls."
                )
            raise ReasoningProviderError(str(exc)) from exc

        message = response.choices[0].message
        tool_calls = list(message.tool_calls or [])

        if tool_calls:
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [tool_call.model_dump(exclude_none=True) for tool_call in tool_calls],
            }
            if message.content not in (None, ""):
                assistant_message["content"] = message.content
            chat_messages.append(assistant_message)

            for tool_call in tool_calls:
                executed_tools = True
                result = _run_tool_call(
                    tool_map,
                    tool_call.function.name,
                    tool_call.function.arguments or "{}",
                )
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
            continue

        text = str(message.content or "").strip()
        if text:
            return text
        return "No textual response was produced. Check logs for tool execution status."

    return "⚠️ DeepSeek reached the tool-call round limit before producing a final answer."


def _probe_gemini(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        payload["error"] = "GOOGLE_API_KEY is not set."
        return payload
    if not HAS_GENAI_SDK:
        payload["error"] = "google-genai package not installed."
        return payload

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_id_for("gemini"),
            contents="Reply with OK",
            config=genai_types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=8,
            ),
        )
        text = str(getattr(response, "text", "") or "").strip()
        payload["response_preview"] = text[:24]
        payload["ok"] = bool(text)
        if not payload["ok"]:
            payload["error"] = "empty model response"
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def _probe_deepseek(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        payload["error"] = "DEEPSEEK_API_KEY is not set."
        return payload
    if not HAS_OPENAI_SDK:
        payload["error"] = "openai package not installed."
        return payload

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=get_deepseek_base_url(),
        )
        response = client.chat.completions.create(
            model=model_id_for("deepseek"),
            messages=[{"role": "user", "content": "Reply with OK"}],
            stream=False,
        )
        text = str(response.choices[0].message.content or "").strip()
        payload["response_preview"] = text[:24]
        payload["ok"] = bool(text)
        if not payload["ok"]:
            payload["error"] = "empty model response"
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def _tool_schema(fn: Callable[..., str]) -> dict[str, Any]:
    description = inspect.getdoc(fn) or f"Call the {fn.__name__} tool."
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description.splitlines()[0],
            "parameters": _tool_parameters_schema(fn),
        },
    }


def _tool_parameters_schema(fn: Callable[..., str]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        annotation = hints.get(name, parameter.annotation)
        schema = _schema_for_annotation(annotation)
        if parameter.default is not inspect.Signature.empty and parameter.default is not None:
            schema["default"] = parameter.default
        properties[name] = schema
        if parameter.default is inspect.Signature.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    annotation = _strip_optional(annotation)
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    origin = get_origin(annotation)
    if origin in (list, tuple, set):
        return {"type": "array", "items": {"type": "string"}}
    if origin is dict:
        return {"type": "object"}
    return {"type": "string"}


def _strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation

    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def _run_tool_call(
    tool_map: dict[str, Callable[..., str]],
    tool_name: str,
    raw_arguments: str,
) -> str:
    fn = tool_map.get(tool_name)
    if fn is None:
        logger.warning("DeepSeek requested unknown tool %s", tool_name)
        return f"Unknown tool: {tool_name}"

    try:
        arguments = json.loads(raw_arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must decode to an object")
    except Exception as exc:
        logger.warning("DeepSeek tool args for %s were invalid: %s", tool_name, exc)
        return f"Invalid tool arguments for {tool_name}: {exc}"

    try:
        coerced = _coerce_tool_arguments(fn, arguments)
        return str(fn(**coerced))
    except Exception as exc:
        logger.exception("DeepSeek tool %s failed", tool_name)
        return f"Tool {tool_name} failed: {exc}"


def _coerce_tool_arguments(fn: Callable[..., str], arguments: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    coerced: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name not in arguments:
            continue
        value = arguments[name]
        annotation = hints.get(name, parameter.annotation)
        coerced[name] = _coerce_value(value, annotation)
    return coerced


def _coerce_value(value: Any, annotation: Any) -> Any:
    target = _strip_optional(annotation)
    if value is None:
        return None
    if target is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if target is int:
        return int(value)
    if target is float:
        return float(value)
    if target is str:
        return str(value)
    return value
