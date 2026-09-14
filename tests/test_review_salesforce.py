import pytest

from src import config, review
from src.models import ApprovedGrantApplication, ReviewStatus
from src.salesforce_adapter import SalesforceAdapter

AMBIGUOUS = "02_ambiguous_youth_center.docx"


def test_cannot_finalize_with_pending_fields(db, new_run):
    run_id = new_run(AMBIGUOUS)
    with db() as s:
        assert not review.can_finalize(review.load_application(s, run_id))
        with pytest.raises(review.ReviewError):
            review.finalize(s, run_id)


def test_bulk_approve_only_touches_high(db, new_run):
    run_id = new_run(AMBIGUOUS)
    with db() as s:
        assert review.approve_all_high(s, run_id) == 1  # only organization_name is High
        app = review.load_application(s, run_id)
    for f in app.fields():
        expected = ReviewStatus.approved if f.confidence_level == "high" else ReviewStatus.pending
        assert f.review_status == expected


def test_edit_outside_taxonomy_rejected(db, new_run):
    run_id = new_run(AMBIGUOUS)
    with db() as s:
        for field, bad in [("grant_type", "Free Money"), ("issues", ["Housing", "Space Travel"])]:
            with pytest.raises(review.ReviewError):
                review.decide(s, run_id, field, "edit", bad)
        review.decide(s, run_id, "grant_type", "edit", "Project Support")  # same as prediction
        assert review.load_application(s, run_id).grant_type.review_status == ReviewStatus.approved


def test_payload_groups_by_object_joins_multipicklist_and_omits_none():
    approved = ApprovedGrantApplication(organization_name="Community Housing Alliance",
                                        grant_type="Project Support", issues=["Housing", "Economic Justice"],
                                        amount_requested=250000)
    assert SalesforceAdapter(config.salesforce_mapping()).to_salesforce(approved) == {
        "Account": {"Name": "Community Housing Alliance"},
        "Grant_Request__c": {"Grant_Type__c": "Project Support",
                             "Issue_Areas__c": "Housing;Economic Justice", "Amount_Requested__c": 250000},
        "_warnings": [],
    }


def test_payload_warns_on_truncation_and_missing_value_map():
    mapping = {"organization_name": {"object": "Account", "field": "Name", "max_length": 5},
               "issues": {"object": "G", "field": "I", "value_map": {"Housing": "HOUSING"}}}
    payload = SalesforceAdapter(mapping).to_salesforce(
        ApprovedGrantApplication(organization_name="Very long name", issues=["Housing", "Health"]))
    assert payload["Account"]["Name"] == "Very " and payload["G"]["I"] == "HOUSING;Health"
    assert len(payload["_warnings"]) == 2
