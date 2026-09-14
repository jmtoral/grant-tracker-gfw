"""Each real provider through its real SDK, with the network replaced by a mock transport."""
import json

import anthropic
import httpx
import httpx2
import openai
import pytest
from conftest import SAMPLES, run_sample
from google import genai
from google.genai import types

from src import config
from src.llm import FakeLLMProvider, make_provider
from src.pipeline import build_schema, run_pipeline

VALID = (config.SAMPLES / "fake_responses" / "01_clear_housing_alliance.json").read_text(encoding="utf-8")


def reply(provider, text):
    if provider == "gemini":
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                "modelVersion": "gemini-served"}
    if provider == "claude":
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-served",
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1}}
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": f"{provider}-served",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def mocked(name, handler):
    p = make_provider(name, "test-key")
    if name == "gemini":
        p.client = genai.Client(api_key="test-key", http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(handler))))
    elif name == "claude":
        p.client = anthropic.Anthropic(api_key="test-key", http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)))
    else:
        p.client = openai.OpenAI(api_key="test-key", base_url=p.base_url, http_client=openai.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)))
    return p


@pytest.mark.parametrize("name", list(config.PROVIDERS))
def test_provider_validates_and_retries_once(name):
    answers = iter(["this is not json", VALID])  # first answer invalid -> exactly one retry
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        module = httpx if name == "gemini" else httpx2
        return module.Response(200, json=reply(name, next(answers)))

    result = mocked(name, handler).extract("[[PAGE 1]]\nDOCUMENT TEXT", build_schema(config.taxonomies()),
                                           "SYSTEM PROMPT")
    assert len(sent) == 2
    assert result.parsed.organization_name.value == "Community Housing Alliance"
    assert result.model_name == config.default_model(name) and result.model_version.endswith("served")
    assert "SYSTEM PROMPT" in sent[0] and "DOCUMENT TEXT" in sent[0]
    body = json.loads(sent[0])
    if name == "claude":  # structured outputs, and no sampling params (rejected by current models)
        assert body["output_config"]["format"]["type"] == "json_schema" and "temperature" not in body
    if name == "openai":
        assert body["response_format"]["type"] == "json_schema"
    if name == "deepseek":  # JSON mode only: the schema must travel inside the prompt
        assert body["response_format"]["type"] == "json_object" and "JSON schema" in sent[0]


def test_without_key_falls_back_to_fake():
    assert isinstance(make_provider("claude", ""), FakeLLMProvider)
    assert isinstance(make_provider("fake", "some-key"), FakeLLMProvider)


@pytest.mark.parametrize("name", list(config.PROVIDERS))
def test_live_provider_on_easy_document(name):
    """Real API call; runs only when that provider's key is in the environment / .env."""
    if not config.env_key(name):
        pytest.skip(f"{config.PROVIDERS[name][0]} not set")
    doc, *_ = run_sample(SAMPLES[0])
    app, result, error = run_pipeline(doc, make_provider(name, config.env_key(name)),
                                      config.settings(), config.taxonomies())
    assert error is None and result.model_version
    evidence = [e for f in app.fields() for e in f.evidence]
    assert evidence and sum(e.verified for e in evidence) / len(evidence) >= 0.7
    json.dumps(result.raw_json)  # raw output is storable
