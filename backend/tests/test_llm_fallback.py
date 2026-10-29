"""Gemini model fallback + cool-down, with a fake client (no network)."""
import pytest

from app.config import settings
from app.detection import llm


class Resp:
    text = '{"scam_score": 91, "reason": "fee to claim a prize", "signals": ["advance fee"]}'


class FakeModels:
    def __init__(self, failing):
        self.failing, self.calls = failing, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model in self.failing:
            raise RuntimeError(self.failing[model])
        return Resp()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "model-a")
    monkeypatch.setattr(settings, "gemini_fallback_models", ["model-b"])
    monkeypatch.setattr(llm, "_cooldown", {})
    models = FakeModels({})
    monkeypatch.setattr(llm, "_client", type("C", (), {"models": models})())
    return models


def test_uses_configured_model_when_it_works(fake):
    r = llm.classify("You won, pay the fee", [], "Prize Desk")
    assert r["score"] == 91 and r["model"] == "model-a"
    assert fake.calls == ["model-a"]


def test_falls_back_on_timeout_and_skips_the_slow_model_afterwards(fake):
    fake.failing["model-a"] = "504 DEADLINE_EXCEEDED. Deadline expired"
    assert llm.classify("You won, pay the fee", [], None)["model"] == "model-b"
    assert llm.classify("You won, pay the fee", [], None)["model"] == "model-b"
    assert fake.calls == ["model-a", "model-b", "model-b"]  # model-a cooling down
    assert "DEADLINE_EXCEEDED" in llm.status()["last_error"]


def test_non_retryable_error_returns_none_for_rules_only(fake):
    fake.failing["model-a"] = "400 API key not valid"
    assert llm.classify("text here", [], None) is None
    assert fake.calls == ["model-a"]
