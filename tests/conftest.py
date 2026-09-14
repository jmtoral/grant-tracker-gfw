import pytest

from src import config
from src.db import save_run, session_factory
from src.ingestion import parse_document
from src.llm import FakeLLMProvider
from src.pipeline import run_pipeline

SAMPLES = ["01_clear_housing_alliance.pdf", "02_ambiguous_youth_center.docx", "03_multi_strategy_coalition.txt"]


def run_sample(name):
    settings = config.settings()
    doc = parse_document(name, (config.SAMPLES / name).read_bytes(), settings)
    app, result, error = run_pipeline(doc, FakeLLMProvider(), settings, config.taxonomies())
    return doc, app, result, error


@pytest.fixture
def db(tmp_path):
    """Session factory over a throwaway SQLite file."""
    return session_factory(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture
def new_run(db):
    def make(name):
        doc, app, result, _ = run_sample(name)
        with db() as s:
            return save_run(s, doc, app, result, "fake", config.settings())
    return make
