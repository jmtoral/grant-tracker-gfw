"""YAML config + .env. Files are re-read on every call so edits apply without a code change."""
import hashlib
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
PROMPT_FILE = ROOT / "prompts" / "grant_intake.md"
SAMPLES = ROOT / "sample_data"

# provider -> (API key env var, model env var, suggested models: first = default, then cheaper ones).
# Keys typed in the UI take precedence over the env; any other model name can be typed in the UI.
PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", ["gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", ["gpt-5.6-terra", "gpt-5.6-luna"]),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", ["deepseek-flash", "deepseek-v4-pro"]),
}

load_dotenv(ROOT / ".env")

# classified field -> taxonomy file
TAXONOMY_FILES = {
    "grant_type": "grant_types.yaml",
    "primary_strategy": "strategies.yaml",
    "issues": "issues.yaml",
    "geography": "geographies.yaml",
    "target_population": "populations.yaml",
}


def _yaml(name: str):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def taxonomies() -> dict[str, list[str]]:
    return {field: _yaml(f) for field, f in TAXONOMY_FILES.items()}


def settings() -> dict:
    return _yaml("settings.yaml")


def salesforce_mapping() -> dict:
    return _yaml("salesforce_mapping.yaml")


def prompt_template() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _hash12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def taxonomy_version() -> str:
    return _hash12(b"".join((CONFIG / f).read_bytes() for f in TAXONOMY_FILES.values()))


def prompt_version() -> str:
    return _hash12(PROMPT_FILE.read_bytes())


def env_key(provider: str) -> str:
    return os.getenv(PROVIDERS[provider][0], "").strip() if provider in PROVIDERS else ""


def models(provider: str) -> list[str]:
    """Suggested models, with the one set in the env (if any) first."""
    _, model_env, suggested = PROVIDERS[provider]
    env = os.getenv(model_env, "").strip()
    return [env] + [m for m in suggested if m != env] if env else suggested


def default_model(provider: str) -> str:
    return models(provider)[0]


def provider_name() -> str:
    """Default provider: LLM_PROVIDER only if its key is in the environment; otherwise the offline fake."""
    wanted = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    return wanted if env_key(wanted) else "fake"


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip() or "sqlite:///data/granter.db"
    prefix = "sqlite:///"
    if url.startswith(prefix) and url != prefix + ":memory:":
        path = Path(url[len(prefix):])
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = prefix + path.as_posix()
    return url
