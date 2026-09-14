"""Upload -> AI -> Confidence -> Evidence -> Human Review -> Approved Record -> Salesforce Payload -> Audit trail."""
import pytest
from conftest import SAMPLES, run_sample

from src import config, review
from src.db import audit_trail, pilot_metrics, save_run
from src.models import FIELDS, ReviewStatus

H, M, L = "high", "medium", "low"
EXPECTED = {  # produced by the real verifier + scorer on the fake answers, nothing forced
    SAMPLES[0]: dict.fromkeys(FIELDS, H),
    SAMPLES[1]: dict(organization_name=H, grant_type=L, primary_strategy=L, issues=M, geography=M,
                     target_population=M, amount_requested=M, project_summary=M),
    SAMPLES[2]: dict(organization_name=H, grant_type=H, primary_strategy=L, issues=M, geography=M,
                     target_population=L, amount_requested=H, project_summary=M),
}
UNVERIFIED = {SAMPLES[0]: 0, SAMPLES[1]: 1, SAMPLES[2]: 0}


@pytest.mark.parametrize("name", SAMPLES)
def test_end_to_end(name, db):
    # AI + confidence + evidence
    doc, app, result, error = run_sample(name)
    assert error is None
    assert {f.field: f.confidence_level.value for f in app.fields()} == EXPECTED[name]
    evidence = [e for f in app.fields() for e in f.evidence]
    assert sum(not e.verified for e in evidence) == UNVERIFIED[name]
    assert all(e.page is not None for e in evidence if e.verified)
    assert all(e.page is None for e in evidence if not e.verified)

    # human review: bulk High, change of mind on grant_type, reject summary, approve the rest
    with db() as s:
        run_id = save_run(s, doc, app, result, "fake", config.settings())
        review.approve_all_high(s, run_id, "tester")
        review.decide(s, run_id, "grant_type", "edit", "Capacity Building", "tester")
        review.decide(s, run_id, "project_summary", "reject", reviewer="tester")
        for f in review.load_application(s, run_id).fields():
            if f.review_status == ReviewStatus.pending:
                review.decide(s, run_id, f.field, "approve", reviewer="tester")
        approved = review.finalize(s, run_id, "tester")
        payload = review.generate_payload(s, run_id)
        trail = audit_trail(s, run_id)
        metrics = pilot_metrics(s)

    # approved record (canonical) and Salesforce payload
    assert approved.grant_type == "Capacity Building" and approved.project_summary is None
    assert approved.organization_name == app.organization_name.predicted_value
    assert approved.issues == app.issues.predicted_value
    assert payload["Account"]["Name"] == approved.organization_name
    sf = payload["Grant_Request__c"]
    assert sf["Grant_Type__c"] == "Capacity Building" and "Project_Summary__c" not in sf
    assert sf["Issue_Areas__c"] == ";".join(approved.issues)
    assert sf["Amount_Requested__c"] == app.amount_requested.predicted_value

    # audit trail: every decision, with versions
    assert len(trail) >= len(FIELDS) and set(trail.field_name) == set(FIELDS)
    assert set(trail.model_name) == {"fake"} and set(trail.model_version) == {"fake"}
    assert set(trail.prompt_version) == {config.prompt_version()}
    assert set(trail.taxonomy_version) == {config.taxonomy_version()}
    latest = trail.drop_duplicates("field_name", keep="last").set_index("field_name")
    assert latest.loc["grant_type", "review_action"] == "edit"
    assert latest.loc["project_summary", "review_action"] == "reject"

    # metrics: 6 of 8 non-null predictions accepted as-is
    assert metrics["acceptance_rate"] == 0.75 and metrics["manual_review_rate"] == 0
    assert metrics["runs_finalized"] == 1 and metrics["fields_decided"] == len(FIELDS)
    bulk = latest.bulk.sum()
    assert metrics["bulk_approvals"] == bulk
    assert metrics["acceptance_rate_excl_bulk"] == ((6 - bulk) / (8 - bulk) if bulk < 8 else None)
