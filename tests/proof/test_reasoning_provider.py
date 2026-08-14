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


def test_deepseek_without_key_returns_inactive_notice(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    reply = rp.ask_with_deepseek(
        [{"role": "user", "content": "hello"}],
        system_instruction="sys",
        tools=[],
        temperature=0.3,
    )
    assert "DEEPSEEK_API_KEY is not set" in reply


def test_probe_without_key_reports_provider_and_error(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    payload = rp.probe_reasoning_model()
    assert payload["ok"] is False
    assert payload["provider"] == "deepseek"
    assert "DEEPSEEK_API_KEY is not set" in payload["error"]


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
