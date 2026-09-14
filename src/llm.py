"""LLM providers. Only this module talks to a model; everything else sees a validated schema instance."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from src import config
from src.models import FIELDS, MULTI_FIELDS


# "the model found nothing": every field null/empty, no evidence -> manual review
NULL_OUTPUT = json.dumps({f: {"value": [] if f in MULTI_FIELDS else None, "evidence": [],
                              "self_confidence": 0} for f in FIELDS})


@dataclass
class LLMResult:
    parsed: BaseModel  # instance of the dynamic schema, already validated
    model_name: str
    model_version: str
    raw_json: str


class LLMProvider(Protocol):
    """To add a model: implement `name` + `extract` and register the class in PROVIDERS."""
    name: str

    def extract(self, document_text: str, schema: type[BaseModel], prompt: str,
                filename: str = "") -> LLMResult: ...


def _validated(call, schema: type[BaseModel], model: str) -> LLMResult:
    """call() -> (raw JSON text, served model version). Never trust the SDK's parsing alone:
    always validate with our schema, retry once on invalid output, then raise."""
    for attempt in range(2):
        try:
            text, version = call()
            return LLMResult(schema.model_validate_json(text or ""), model, version or model, text)
        except ValidationError:
            if attempt:
                raise


# SDKs are imported lazily: the fake path needs none of them.
# Every provider sends the prompt as the system message and the untrusted document as user content.
class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def extract(self, document_text, schema, prompt, filename=""):
        from google.genai import types
        cfg = types.GenerateContentConfig(system_instruction=prompt, temperature=0,
                                          response_mime_type="application/json", response_schema=schema)

        def call():
            r = self.client.models.generate_content(model=self.model, contents=document_text, config=cfg)
            return r.text, r.model_version
        return _validated(call, schema, self.model)


class ClaudeProvider:
    """Anthropic structured outputs. No temperature: current Claude models reject sampling params."""
    name = "claude"

    def __init__(self, api_key: str, model: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def extract(self, document_text, schema, prompt, filename=""):
        def call():
            r = self.client.messages.parse(model=self.model, max_tokens=16000, system=prompt,
                                           messages=[{"role": "user", "content": document_text}],
                                           output_format=schema)
            # a refusal carries no text block -> fails validation -> manual review
            return next((b.text for b in r.content if b.type == "text"), ""), r.model
        return _validated(call, schema, self.model)


class OpenAIProvider:
    name = "openai"
    base_url = None  # official API

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)
        self.model = model

    def _format(self, schema, prompt):
        # non-strict: strict mode rejects some of our constraints; our own validation is the real gate
        return prompt, {"type": "json_schema",
                        "json_schema": {"name": "grant_intake", "schema": schema.model_json_schema()}}

    def extract(self, document_text, schema, prompt, filename=""):
        system, response_format = self._format(schema, prompt)

        def call():
            r = self.client.chat.completions.create(
                model=self.model, response_format=response_format,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": document_text}])
            return r.choices[0].message.content, r.model
        return _validated(call, schema, self.model)


class DeepSeekProvider(OpenAIProvider):
    """OpenAI-compatible API, but JSON mode only (no json_schema): the schema travels in the prompt."""
    name = "deepseek"
    base_url = "https://api.deepseek.com"

    def _format(self, schema, prompt):
        return (f"{prompt}\n\nReply with a single JSON object that follows this JSON schema:\n"
                f"{json.dumps(schema.model_json_schema())}"), {"type": "json_object"}


class FakeLLMProvider:
    """Deterministic, offline. Answers from sample_data/fake_responses/<file stem>.json."""
    name = "fake"

    def __init__(self, responses_dir: Path = config.SAMPLES / "fake_responses"):
        self.responses_dir = responses_dir

    def extract(self, document_text, schema, prompt, filename=""):
        path = self.responses_dir / f"{Path(filename).stem}.json"
        raw = path.read_text(encoding="utf-8") if path.is_file() else NULL_OUTPUT  # unknown doc
        return LLMResult(schema.model_validate_json(raw), "fake", "fake", raw)


PROVIDERS = {p.name: p for p in (GeminiProvider, ClaudeProvider, OpenAIProvider, DeepSeekProvider)}


def make_provider(name: str, api_key: str = "", model: str = "") -> LLMProvider:
    """The requested provider, or the offline fake when there is no key to use it."""
    if name not in PROVIDERS or not api_key:
        return FakeLLMProvider()
    return PROVIDERS[name](api_key, model or config.default_model(name))
