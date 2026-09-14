"""Human review: every decision is persisted immediately (append-only); the latest one per field wins."""
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src import config
from src.db import ApprovedRecord, Prediction, ReviewDecision, Run, now
from src.models import (MULTI_FIELDS, ApprovedGrantApplication, ConfidenceLevel, FieldReview,
                        GrantApplication, MultiValueFieldReview, ReviewStatus, is_empty)
from src.salesforce_adapter import SalesforceAdapter

ACTION_STATUS = {"approve": ReviewStatus.approved, "edit": ReviewStatus.edited,
                 "reject": ReviewStatus.rejected}
OPEN = {ReviewStatus.pending, ReviewStatus.manual_review}


class ReviewError(ValueError):
    pass


def load_application(session: Session, run_id: int) -> GrantApplication:
    """Rebuild predictions + current review state from the DB (the source of truth)."""
    run = session.get(Run, run_id)
    latest = {d.field: d for d in session.scalars(
        select(ReviewDecision).where(ReviewDecision.run_id == run_id).order_by(ReviewDecision.id))}
    reviews = {}
    for p in session.scalars(select(Prediction).where(Prediction.run_id == run_id)):
        d = latest.get(p.field)
        status = ACTION_STATUS[d.action] if d else (
            ReviewStatus.manual_review if is_empty(p.predicted_value) else ReviewStatus.pending)
        reviews[p.field] = (MultiValueFieldReview if p.field in MULTI_FIELDS else FieldReview)(
            field=p.field, predicted_value=p.predicted_value, confidence=p.confidence,
            confidence_level=p.confidence_level, confidence_method=run.confidence_method,
            evidence=p.evidence, review_status=status, reviewed_value=d.reviewed_value if d else None)
    return GrantApplication(**reviews)


def decide(session: Session, run_id: int, field: str, action: str, value=None,
           reviewer: str = "", bulk: bool = False) -> None:
    run = session.get(Run, run_id)
    if run.finalized_at:
        raise ReviewError("This review is already finalized.")
    predicted = session.get(Prediction, (run_id, field)).predicted_value
    if action == "approve":
        if is_empty(predicted):
            raise ReviewError("Nothing to approve: edit or reject this field.")
        value = predicted
    elif action == "reject":
        value = [] if field in MULTI_FIELDS else None
    elif action == "edit":
        if isinstance(value, str):
            value = value.strip()
        if is_empty(value):
            raise ReviewError("An edit needs a value. Use Reject to leave the field empty.")
        try:  # same validator as the canonical record: taxonomy + types
            value = getattr(ApprovedGrantApplication(**{field: value}), field)
        except ValidationError as e:
            raise ReviewError(f"Invalid value: {e.errors()[0]['msg']}") from None
        same = sorted(value) == sorted(predicted or []) if field in MULTI_FIELDS else value == predicted
        if same:
            action = "approve"  # an "edit" that keeps the prediction is an approval
    else:
        raise ReviewError(f"Unknown action {action!r}.")
    session.add(ReviewDecision(run_id=run_id, field=field, action=action, reviewed_value=value,
                               bulk=bulk, reviewer=reviewer or None))
    session.commit()


def approve_all_high(session: Session, run_id: int, reviewer: str = "") -> int:
    """Approve only still-pending High fields; Medium/Low always need an individual decision."""
    todo = [f.field for f in load_application(session, run_id).fields()
            if f.confidence_level == ConfidenceLevel.high and f.review_status == ReviewStatus.pending]
    for field in todo:
        decide(session, run_id, field, "approve", reviewer=reviewer, bulk=True)
    return len(todo)


def can_finalize(app: GrantApplication) -> bool:
    return all(f.review_status not in OPEN for f in app.fields())


def finalize(session: Session, run_id: int, reviewer: str = "") -> ApprovedGrantApplication:
    app = load_application(session, run_id)
    if not can_finalize(app):
        raise ReviewError("Every field needs a decision before finalizing.")
    approved = ApprovedGrantApplication(**{f.field: f.reviewed_value for f in app.fields()})
    run = session.get(Run, run_id)
    run.status, run.finalized_at, run.reviewer = "finalized", now(), reviewer or None
    session.add(ApprovedRecord(run_id=run_id, canonical_json=approved.model_dump()))
    session.commit()
    return approved


def generate_payload(session: Session, run_id: int) -> dict:
    record = session.get(ApprovedRecord, run_id)
    if record is None:
        raise ReviewError("Finalize the review first: only approved records produce a payload.")
    payload = SalesforceAdapter(config.salesforce_mapping()).to_salesforce(
        ApprovedGrantApplication(**record.canonical_json))
    record.salesforce_payload = payload
    session.commit()
    return payload
