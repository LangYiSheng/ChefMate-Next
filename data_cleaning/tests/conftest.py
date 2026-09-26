import pytest


@pytest.fixture(autouse=True)
def isolate_ai_configuration(monkeypatch):
    """Tests must not inherit the developer's selected provider or credentials."""
    monkeypatch.setenv("OPENAI_API_MODE", "responses")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
