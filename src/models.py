from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src import config

FIELDS = [
    "organization_name", "grant_type", "primary_strategy", "issues",
    "geography", "target_population", "amount_requested", "project_summary",
]
MULTI_FIELDS = {"issues", "geography", "target_population"}
CLASSIFIED_FIELDS = set(config.TAXONOMY_FILES)  # grant_type, primary_strategy + the multi fields


def is_empty(value) -> bool:
    """No usable value: null, empty list or blank text."""
    return value is None or value == [] or value == ""


class ReviewStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    edited = "edited"
    rejected = "rejected"
    manual_review = "manual_review"


class EvidenceType(StrEnum):
    explicit = "explicit"
    inferred = "inferred"


class ConfidenceLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class Evidence(BaseModel):
    text: str                 # quote as returned by the model
    page: int | None          # page RECOMPUTED by our verifier, never the model's claim
    evidence_type: EvidenceType
    verified: bool            # quote was found in the document


class FieldReview(BaseModel):
    field: str
    predicted_value: str | float | None
    confidence: float
    confidence_level: ConfidenceLevel
    confidence_method: str
    evidence: list[Evidence]
    review_status: ReviewStatus = ReviewStatus.pending
    reviewed_value: str | float | None = None


class MultiValueFieldReview(FieldReview):
    predicted_value: list[str]
    reviewed_value: list[str] | None = None


class GrantApplication(BaseModel):
    """AI PREDICTION object. Never sent anywhere without a human decision per field."""
    organization_name: FieldReview
    grant_type: FieldReview
    primary_strategy: FieldReview
    issues: MultiValueFieldReview
    geography: MultiValueFieldReview
    target_population: MultiValueFieldReview
    amount_requested: FieldReview
    project_summary: FieldReview

    def fields(self) -> list[FieldReview]:
        return [getattr(self, f) for f in FIELDS]


class ApprovedGrantApplication(BaseModel):
    """HUMAN-APPROVED canonical record. Classified values must belong to the current taxonomy."""
    organization_name: str | None = None
    grant_type: str | None = None
    primary_strategy: str | None = None
    issues: list[str] = []
    geography: list[str] = []
    target_population: list[str] = []
    amount_requested: float | None = Field(None, ge=0)
    project_summary: str | None = None

    @field_validator(*config.TAXONOMY_FILES)
    @classmethod
    def _in_taxonomy(cls, value, info):
        allowed = config.taxonomies()[info.field_name]
        values = value if isinstance(value, list) else [value]
        bad = [v for v in values if v is not None and v not in allowed]
        if bad:
            raise ValueError(f"not in taxonomy {info.field_name}: {bad}")
        return value
