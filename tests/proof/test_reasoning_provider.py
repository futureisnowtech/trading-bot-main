"""Proof: the reasoning provider resolves Gemini vs DeepSeek without live calls.

The live brain, the cockpit regime manifest, and the release-audit model probe
all resolve their backend through runtime.reasoning_provider, so a silent
regression here shows up as telegram_model_probe_failed on a deploy.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

import runtime.reasoning_provider as rp


def test_provider_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("REASONING_PROVIDER", raising=False)
    assert rp.get_reasoning_provider() == "gemini"


def test_unknown_provider_falls_back_to_gemini(monkeypatch):
    """A typo in .env must not silently disable the brain."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepsekk")
    assert rp.get_reasoning_provider() == "gemini"
    assert rp.get_reasoning_model_id().startswith("models/")


def test_gemini_model_id_is_namespaced(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_REASONING_MODEL", "gemini-2.5-flash")
    assert rp.get_reasoning_model_id() == "models/gemini-2.5-flash"


def test_deepseek_model_id_is_bare(monkeypatch):
    """DeepSeek speaks the OpenAI wire format, where model ids carry no prefix."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    assert rp.get_reasoning_model_id() == "deepseek-v4-flash"


def test_display_name_carries_the_provider(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert rp.get_reasoning_display_name() == "deepseek:deepseek-v4-pro"


def test_effort_and_thinking_fall_back_to_config_defaults(monkeypatch):
    """config.py owns these defaults; empty env must not drop them from the request."""
    monkeypatch.delenv("DEEPSEEK_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("DEEPSEEK_THINKING_MODE", raising=False)
    monkeypatch.setattr(rp, "DEEPSEEK_REASONING_EFFORT", "high", raising=False)
    monkeypatch.setattr(rp, "DEEPSEEK_THINKING_MODE", "enabled", raising=False)
    assert rp.get_deepseek_reasoning_effort() == "high"
    assert rp.get_deepseek_thinking_mode() == "enabled"


def test_invalid_effort_and_thinking_are_omitted(monkeypatch):
    """An unrecognised value must be dropped, not forwarded into a 400 from the API."""
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "maximum")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "on")
    assert rp.get_deepseek_reasoning_effort() == ""
    assert rp.get_deepseek_thinking_mode() == ""


def test_probe_with_neither_provider_configured_reports_both(monkeypatch):
    """Both keys blanked so the probe cannot reach the network from a test run."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    payload = rp.probe_reasoning_model()
    assert payload["ok"] is False
    assert payload["provider"] == "deepseek"
    assert "DEEPSEEK_API_KEY is not set" in payload["error"]
    assert "GOOGLE_API_KEY is not set" in payload["error"]


def test_tool_schema_marks_defaulted_params_optional():
    """The brain's tools carry defaults; requiring them would break every call."""

    def fetch_rows(ticker: str, limit: int = 10, verbose: bool = False) -> str:
        """Fetch settlement rows for a ticker."""
        return ""

    schema = rp._tool_schema(fetch_rows)
    params = schema["function"]["parameters"]
    assert schema["function"]["name"] == "fetch_rows"
    assert schema["function"]["description"] == "Fetch settlement rows for a ticker."
    assert params["required"] == ["ticker"]
    assert params["properties"]["limit"]["type"] == "integer"
    assert params["properties"]["verbose"]["type"] == "boolean"


def test_tool_call_coerces_string_arguments():
    """Models hand back JSON strings; ints and bools must survive the trip."""
    seen = {}

    def fetch_rows(ticker: str, limit: int = 10, verbose: bool = False) -> str:
        """Fetch settlement rows for a ticker."""
        seen.update({"ticker": ticker, "limit": limit, "verbose": verbose})
        return "ok"

    result = rp._run_tool_call(
        {"fetch_rows": fetch_rows},
        "fetch_rows",
        '{"ticker": "KXHIGHNY", "limit": "5", "verbose": "true"}',
    )
    assert result == "ok"
    assert seen == {"ticker": "KXHIGHNY", "limit": 5, "verbose": True}


def test_unknown_tool_is_reported_not_raised():
    assert "Unknown tool" in rp._run_tool_call({}, "drop_tables", "{}")


def test_malformed_tool_arguments_are_reported_not_raised():
    def noop(ticker: str) -> str:
        """Do nothing."""
        return ""

    assert "Invalid tool arguments" in rp._run_tool_call({"noop": noop}, "noop", "{not json")


def test_missing_key_raises_so_caller_can_cascade(monkeypatch):
    """A missing key must raise, not return prose -- prose can't trigger a fallback."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with pytest.raises(rp.ReasoningProviderError):
        rp.ask_with_deepseek(
            [{"role": "user", "content": "hello"}],
            system_instruction="sys",
            tools=[],
            temperature=0.3,
        )


def test_api_failure_before_any_tool_ran_raises_for_cascade(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(rp, "HAS_OPENAI_SDK", True, raising=False)

    class _Boom:
        def __init__(self, *a, **k):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise RuntimeError("Insufficient Balance")

    monkeypatch.setattr(rp, "OpenAI", _Boom, raising=False)
    with pytest.raises(rp.ReasoningProviderError, match="Insufficient Balance"):
        rp.ask_with_deepseek(
            [{"role": "user", "content": "hi"}],
            system_instruction="sys",
            tools=[],
            temperature=0.3,
        )


def test_api_failure_after_a_tool_ran_does_not_cascade(monkeypatch):
    """Re-running the conversation on Gemini would repeat a tool that already took effect."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(rp, "HAS_OPENAI_SDK", True, raising=False)

    calls = {"n": 0}
    ran = []

    def side_effect_tool(value: str) -> str:
        """A tool with a side effect."""
        ran.append(value)
        return "done"

    class _ToolCall:
        id = "call_1"

        class function:
            name = "side_effect_tool"
            arguments = '{"value": "x"}'

        def model_dump(self, exclude_none=False):
            return {"id": "call_1", "type": "function"}

    class _Msg:
        content = None
        tool_calls = [_ToolCall()]

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    class _Client:
        def __init__(self, *a, **k):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp()
            raise RuntimeError("Insufficient Balance")

    monkeypatch.setattr(rp, "OpenAI", _Client, raising=False)
    reply = rp.ask_with_deepseek(
        [{"role": "user", "content": "hi"}],
        system_instruction="sys",
        tools=[side_effect_tool],
        temperature=0.3,
    )
    assert ran == ["x"], "the tool should have run exactly once"
    assert "Not retrying on the fallback provider" in reply


def test_probe_falls_back_to_gemini_when_deepseek_is_down(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setattr(
        rp, "_probe_deepseek",
        lambda p: {**p, "ok": False, "error": "Insufficient Balance"},
        raising=False,
    )
    monkeypatch.setattr(
        rp, "_probe_gemini",
        lambda p: {**p, "ok": True, "response_preview": "OK"},
        raising=False,
    )
    payload = rp.probe_reasoning_model()
    assert payload["ok"] is True
    assert payload["fallback_used"] is True
    assert payload["fallback_provider"] == "gemini"
    assert "Insufficient Balance" in payload["error"]


def test_probe_reports_failure_only_when_both_providers_are_down(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setattr(rp, "_probe_deepseek", lambda p: {**p, "ok": False, "error": "ds down"}, raising=False)
    monkeypatch.setattr(rp, "_probe_gemini", lambda p: {**p, "ok": False, "error": "gm down"}, raising=False)
    payload = rp.probe_reasoning_model()
    assert payload["ok"] is False
    assert "ds down" in payload["error"] and "gm down" in payload["error"]


def test_each_probe_uses_its_own_model_id(monkeypatch):
    """Under fallback the configured provider is still deepseek; the Gemini probe
    must not be handed a DeepSeek model name."""
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("GEMINI_REASONING_MODEL", "gemini-2.5-flash")
    assert rp.model_id_for("deepseek") == "deepseek-v4-flash"
    assert rp.model_id_for("gemini") == "models/gemini-2.5-flash"
