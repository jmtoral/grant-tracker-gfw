"""SQLAlchemy tables + audit/metrics queries. Document content, raw model output and audit metadata
live in separate tables. Moving to PostgreSQL = change DATABASE_URL (JSON type is portable)."""
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from src import APP_VERSION, config
from src.ingestion import ParsedDocument
from src.llm import LLMResult
from src.models import GrantApplication, is_empty


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of bytes
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))
    page_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentPage(Base):  # CONTENT (the original file is never stored)
    __tablename__ = "document_pages"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(12))
    taxonomy_version: Mapped[str] = mapped_column(String(12))
    app_version: Mapped[str] = mapped_column(String(16))
    confidence_method: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # in_review | llm_failed | finalized
    review_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(String(128))


class ModelOutput(Base):  # RAW MODEL OUTPUT
    __tablename__ = "model_outputs"
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    raw_json: Mapped[str] = mapped_column(Text)


class Prediction(Base):
    __tablename__ = "predictions"
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    field: Mapped[str] = mapped_column(String(32), primary_key=True)
    predicted_value = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_level: Mapped[str] = mapped_column(String(8))
    evidence = mapped_column(JSON)


class ReviewDecision(Base):  # AUDIT: append-only, the latest row per (run, field) is current
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    field: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(8))  # approve | edit | reject
    reviewed_value = mapped_column(JSON)
    bulk: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApprovedRecord(Base):
    __tablename__ = "approved_records"
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    canonical_json = mapped_column(JSON)
    salesforce_payload = mapped_column(JSON)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def session_factory(url: str | None = None) -> sessionmaker:
    engine = create_engine(url or config.database_url())
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def save_run(session: Session, doc: ParsedDocument, app: GrantApplication,
             result: LLMResult | None, provider: str, settings: dict) -> int:
    if session.get(Document, doc.document_id) is None:
        session.add(Document(id=doc.document_id, filename=doc.filename, file_type=doc.file_type,
                             page_count=len(doc.pages)))
        session.add_all(DocumentPage(document_id=doc.document_id, page_number=i, text=t)
                        for i, t in enumerate(doc.pages, 1))
    run = Run(document_id=doc.document_id, provider=provider,
              model_name=result.model_name if result else provider,
              model_version=result.model_version if result else "n/a",
              prompt_version=config.prompt_version(), taxonomy_version=config.taxonomy_version(),
              app_version=APP_VERSION, confidence_method=settings["confidence"]["method"],
              status="in_review" if result else "llm_failed")
    session.add(run)
    session.flush()
    if result:
        session.add(ModelOutput(run_id=run.id, raw_json=result.raw_json))
    session.add_all(Prediction(run_id=run.id, field=f.field, predicted_value=f.predicted_value,
                               confidence=f.confidence, confidence_level=f.confidence_level,
                               evidence=[e.model_dump(mode="json") for e in f.evidence])
                    for f in app.fields())
    session.commit()
    return run.id


def document_pages(session: Session, document_id: str) -> list[str]:
    return list(session.scalars(select(DocumentPage.text).where(DocumentPage.document_id == document_id)
                                .order_by(DocumentPage.page_number)))


def audit_trail(session: Session, run_id: int | None = None) -> pd.DataFrame:
    """One row per human decision, joined with its prediction and run versions."""
    q = (select(ReviewDecision.id.label("decision_id"), Run.id.label("run_id"), Run.document_id,
                Document.filename, ReviewDecision.field.label("field_name"), Prediction.predicted_value,
                Prediction.confidence, Prediction.confidence_level, ReviewDecision.reviewed_value,
                ReviewDecision.action.label("review_action"), ReviewDecision.bulk,
                ReviewDecision.reviewer, Prediction.evidence, ReviewDecision.decided_at.label("timestamp"),
                Run.model_name, Run.model_version, Run.prompt_version, Run.taxonomy_version)
         .join(Run, Run.id == ReviewDecision.run_id)
         .join(Document, Document.id == Run.document_id)
         .join(Prediction, (Prediction.run_id == ReviewDecision.run_id)
               & (Prediction.field == ReviewDecision.field))
         .order_by(ReviewDecision.id))
    if run_id is not None:
        q = q.where(Run.id == run_id)
    return pd.DataFrame([r._asdict() for r in session.execute(q)],
                        columns=[c.name for c in q.selected_columns])


def pilot_metrics(session: Session) -> dict | None:
    """Metrics over finalized runs, using the LATEST decision per field. Human decision = truth."""
    runs = session.scalars(select(Run).where(Run.finalized_at.is_not(None))).all()
    df = audit_trail(session)
    df = df[df.run_id.isin([r.id for r in runs])].drop_duplicates(["run_id", "field_name"], keep="last")
    if df.empty:
        return None
    empty = df.predicted_value.map(is_empty)
    predicted = df[~empty].assign(accepted=lambda d: d.review_action == "approve")
    no_bulk = predicted[~predicted.bulk]
    low = predicted[predicted.confidence_level == "low"]
    by_field = (predicted.groupby("field_name")
                .agg(decided=("accepted", "size"), acceptance_rate=("accepted", "mean"))
                .assign(correction_rate=lambda d: 1 - d.acceptance_rate))
    seconds = [(r.finalized_at - r.review_started_at).total_seconds() for r in runs]

    def rate(s):
        return float(s.mean()) if len(s) else None

    return {
        "acceptance_rate": rate(predicted.accepted),
        "acceptance_rate_excl_bulk": rate(no_bulk.accepted),
        "fields_decided": len(predicted),
        "bulk_approvals": int(predicted.bulk.sum()),
        "manual_review_rate": float(empty.mean()),
        "low_confidence_error_rate": rate(~low.accepted),
        "avg_confidence_accepted": rate(predicted[predicted.accepted].confidence),
        "avg_confidence_corrected": rate(predicted[~predicted.accepted].confidence),
        "avg_review_seconds": sum(seconds) / len(seconds),
        "runs_finalized": len(runs),
        "by_field": by_field,
    }
