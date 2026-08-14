import importlib


def test_ai_agent_uses_repo_gemini_model_by_default(monkeypatch):
    monkeypatch.delenv("REASONING_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_REASONING_MODEL", raising=False)

    import notifications.ai_agent as ai_agent

    ai_agent = importlib.reload(ai_agent)
    assert ai_agent.get_reasoning_model_id() == "models/gemini-2.5-flash"


def test_ai_agent_normalizes_prefixed_override(monkeypatch):
    monkeypatch.delenv("REASONING_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_REASONING_MODEL", "models/gemini-2.5-flash")

    import notifications.ai_agent as ai_agent

    ai_agent = importlib.reload(ai_agent)
    assert ai_agent.get_reasoning_model_id() == "models/gemini-2.5-flash"


def test_ai_agent_uses_deepseek_model_when_provider_is_enabled(monkeypatch):
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    import notifications.ai_agent as ai_agent

    ai_agent = importlib.reload(ai_agent)
    assert ai_agent.get_reasoning_model_id() == "deepseek-v4-flash"
