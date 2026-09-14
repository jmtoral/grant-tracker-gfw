import json
from types import SimpleNamespace as P

import pytest
from pydantic import ValidationError

from src import config
from src.confidence import level, score
from src.ingestion import ParsedDocument
from src.llm import NULL_OUTPUT, FakeLLMProvider
from src.models import Evidence, ReviewStatus
from src.pipeline import build_schema, run_pipeline, verify_quote

S = config.settings()
PAGES = ["Intro page.", "The Fair Rent Campaign is a statewide effort. We don't charge fees.", "Budget page."]


def test_schema_rejects_value_outside_taxonomy():
    schema = build_schema(config.taxonomies())
    data = json.loads(NULL_OUTPUT)
    data["grant_type"]["value"] = "Project Support"
    schema.model_validate(data)
    for field, bad in [("grant_type", "Free Money"), ("issues", ["Housing", "Space Travel"])]:
        broken = json.loads(json.dumps(data))
        broken[field]["value"] = bad
        with pytest.raises(ValidationError):
            schema.model_validate(broken)


def test_literal_quote_verified_on_real_page():
    assert verify_quote("the fair  rent campaign is a STATEWIDE effort", PAGES, 0.85) == (True, 2)
    assert verify_quote("We don’t charge fees", PAGES, 0.85) == (True, 2)  # typographic apostrophe
    assert verify_quote("The Fair Rent Campain is a statewide efort", PAGES, 0.85) == (True, 2)  # fuzzy


def test_invented_quote_is_unverified():
    assert verify_quote("We will build a rocket to Mars next year", PAGES, 0.85) == (False, None)


def ev(text, kind="explicit", verified=True):
    return Evidence(text=text, page=1 if verified else None, evidence_type=kind, verified=verified)


def test_confidence_caps_and_levels():
    strong = [ev("this is a request for project support"), ev("project support for the campaign")]
    assert score("grant_type", P(value="Project Support", self_confidence=1.0), strong, S) == 1.0
    # classified field without verified evidence -> at most 0.60
    assert score("grant_type", P(value="Project Support", self_confidence=1.0),
                 [ev("project support", verified=False)], S) <= 0.60
    # Other -> at most 0.60, even with perfect evidence
    assert score("grant_type", P(value="Other", self_confidence=1.0), [ev("other"), ev("other")], S) == 0.60
    # null -> 0
    assert score("organization_name", P(value=None, self_confidence=1.0), [], S) == 0
    assert [level(c, S) for c in (0.9, 0.8999, 0.7, 0.6999)] == ["high", "medium", "medium", "low"]


def test_unknown_document_goes_to_manual_review():
    doc = ParsedDocument("x" * 64, "unknown.txt", "txt", ["Some text."])
    app, _, error = run_pipeline(doc, FakeLLMProvider(), S, config.taxonomies())
    assert error is None
    assert all(f.review_status == ReviewStatus.manual_review and f.confidence == 0 for f in app.fields())


def test_llm_failure_never_raises_and_hides_content():
    class Broken:
        name = "broken"

        def extract(self, *a, **k):
            raise RuntimeError("SECRET DOCUMENT CONTENT")

    doc = ParsedDocument("x" * 64, "a.txt", "txt", ["Some text."])
    app, result, error = run_pipeline(doc, Broken(), S, config.taxonomies())
    assert result is None and "RuntimeError" in error and "SECRET" not in error
    assert all(f.review_status == ReviewStatus.manual_review for f in app.fields())
