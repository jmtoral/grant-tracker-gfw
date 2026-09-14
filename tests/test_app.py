from streamlit.testing.v1 import AppTest

from src import config


def test_review_flow_in_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception

    at.selectbox(key="sample").select("01_clear_housing_alliance.pdf").run()
    at.button(key="run").click().run()
    assert not at.exception
    run_id = at.session_state["run_id"]

    at.text_input(key=f"w_{run_id}_organization_name_pending").input("Community Housing Alliance Inc.").run()
    at.button(key=f"edit_{run_id}_organization_name").click().run()
    at.button(key="bulk").click().run()
    at.button(key="finalize").click().run()
    at.button(key="sf_payload").click().run()
    assert not at.exception

    bodies = [j.value for j in at.json]
    assert any("Community Housing Alliance Inc." in b and "Grant_Request__c" in b for b in bodies)

    at.sidebar.radio(key="nav").set_value("Pilot metrics").run()
    at.sidebar.radio(key="nav").set_value("Audit trail").run()
    assert not at.exception
