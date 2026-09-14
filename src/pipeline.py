"""Document -> one structured LLM call -> verified evidence -> heuristic confidence -> GrantApplication."""
# ponytail: una sola pasada; separar extracción/clasificación si la calidad por campo lo exige
from difflib import SequenceMatcher
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, create_model

from src import config
from src.confidence import level, normalize, score
from src.ingestion import ParsedDocument
from src.llm import NULL_OUTPUT, LLMProvider, LLMResult
from src.models import (FIELDS, MULTI_FIELDS, Evidence, EvidenceType, FieldReview,
                        GrantApplication, MultiValueFieldReview, ReviewStatus, is_empty)


class Quote(BaseModel):
    text: str = Field(max_length=300)
    page: int | None = None  # the model's claim; ignored, we recompute it
    evidence_type: EvidenceType


def build_schema(taxonomies: dict[str, list[str]]) -> type[BaseModel]:
    """Output schema built from the YAML at runtime: classified values are Literal[...] so
    anything outside the taxonomy fails validation, even after the YAML changes."""
    value_types = {
        "organization_name": Optional[str],
        "amount_requested": Optional[float],
        "project_summary": Optional[Annotated[str, Field(max_length=600)]],
    }
    for field, values in taxonomies.items():
        allowed = Literal[tuple(values)]
        value_types[field] = list[allowed] if field in MULTI_FIELDS else Optional[allowed]
    per_field = {
        f: (create_model(f"{f}_output",
                         value=(value_types[f], ...),
                         evidence=(list[Quote], Field(max_length=3)),
                         self_confidence=(float, Field(ge=0, le=1))), ...)
        for f in FIELDS
    }
    return create_model("GrantIntakeOutput", **per_field)


def render_prompt(taxonomies: dict[str, list[str]]) -> str:
    lists = "\n".join(f"- {field}: " + " | ".join(values) for field, values in taxonomies.items())
    return config.prompt_template().replace("{taxonomies}", lists)


def document_text(doc: ParsedDocument) -> str:
    return "\n\n".join(f"[[PAGE {i}]]\n{text}" for i, text in enumerate(doc.pages, 1))


def verify_quote(quote: str, pages: list[str], min_ratio: float) -> tuple[bool, int | None]:
    """Find the quote in the document. Returns (verified, real 1-based page)."""
    q = normalize(quote).strip(" \"'…")
    if not q:
        return False, None
    norm_pages = [normalize(p) for p in pages]
    for i, page in enumerate(norm_pages, 1):
        if q in page:
            return True, i
    # ponytail: fuzzy O(n·m) con difflib; cambiar a búsqueda indexada si hay documentos >100 páginas
    sm = SequenceMatcher(autojunk=False)
    sm.set_seq2(q)  # difflib caches seq2: slide windows through seq1
    n = len(q)
    for i, page in enumerate(norm_pages, 1):
        for start in range(max(1, len(page) - n + 1)):
            sm.set_seq1(page[start:start + n])
            if sm.real_quick_ratio() >= min_ratio and sm.quick_ratio() >= min_ratio \
                    and sm.ratio() >= min_ratio:
                return True, i
    return False, None


def run_pipeline(doc: ParsedDocument, provider: LLMProvider, settings: dict,
                 taxonomies: dict[str, list[str]]) -> tuple[GrantApplication, LLMResult | None, str | None]:
    """Returns (predictions, llm result or None, user-facing error or None). Never raises on LLM failure."""
    schema = build_schema(taxonomies)
    try:
        result = provider.extract(document_text(doc), schema, render_prompt(taxonomies),
                                  filename=doc.filename)
        output, error = result.parsed, None
    except Exception as e:  # invalid output after retry, network, auth... never echo content
        code = getattr(e, "status_code", None) or getattr(e, "code", None)  # HTTP status, e.g. 401 bad key
        result, output = None, schema.model_validate_json(NULL_OUTPUT)
        error = (f"LLM extraction failed ({type(e).__name__}{f' {code}' if code else ''}). "
                 "All fields need manual review.")

    ratio = settings["evidence"]["fuzzy_ratio"]
    reviews = {}
    for f in FIELDS:
        pred = getattr(output, f)
        evidence = []
        for q in pred.evidence:
            ok, page = verify_quote(q.text, doc.pages, ratio)
            evidence.append(Evidence(text=q.text, page=page, evidence_type=q.evidence_type, verified=ok))
        conf = score(f, pred, evidence, settings)
        reviews[f] = (MultiValueFieldReview if f in MULTI_FIELDS else FieldReview)(
            field=f, predicted_value=pred.value, confidence=conf,
            confidence_level=level(conf, settings),
            confidence_method=settings["confidence"]["method"], evidence=evidence,
            review_status=ReviewStatus.manual_review if is_empty(pred.value) else ReviewStatus.pending,
        )
    return GrantApplication(**reviews), result, error
