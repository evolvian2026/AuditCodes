"""The review app end to end: upload, browse, edit, verify."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auditcodes.app import create_app
from auditcodes.edits import EditError, apply_edit
from auditcodes.models import Language

from .conftest import require_language

FIXTURE = Path(__file__).parent / "fixtures" / "coding_ques_sample.pdf"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("jobs"))
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def job_id(client):
    r = client.post("/jobs", files={"file": ("coding_ques_sample.pdf", FIXTURE.read_bytes(), "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[1]


def test_index_and_upload_validation(client):
    assert client.get("/").status_code == 200
    r = client.post("/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_job_overview(client, job_id):
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert "AGENT RA" in r.text and "Sunehri and her Bag" in r.text
    assert "105376" in r.text and "Greedy Algorithms" in r.text
    index = client.get("/").text
    assert "coding_ques_sample.pdf" in index


def test_question_page_shows_everything(client, job_id):
    r = client.get(f"/jobs/{job_id}/q/105376")
    assert r.status_code == 200
    html = r.text
    assert "typographic characters in code" in html  # extraction warning surfaced
    assert "non-empty string <strong>s</strong>" in html  # markdown rendered
    assert "public static String ancientTranslation" in html
    assert "Test Case 10" in html and "?b?b?b?b?b" in html
    assert "next</a>" in html and "prev</a>" not in html
    assert client.get(f"/jobs/{job_id}/q/nope").status_code == 404


def test_edit_persists_and_validates(client, job_id):
    r = client.post(f"/jobs/{job_id}/q/105376/edit", data={"path": "title", "value": "Agent RA (edited)"})
    assert r.status_code == 200 and "saved" in r.text
    assert "Agent RA (edited)" in client.get(f"/jobs/{job_id}").text
    r = client.post(f"/jobs/{job_id}/q/105376/edit", data={"path": "constraints", "value": "1 <= |s| <= 50\n\ns has only a, b, ?"})
    assert "1 &lt;= |s| &lt;= 50" in r.text
    data = json.loads(client.get(f"/jobs/{job_id}/export.json").content)
    q = next(x for x in data if x["id"] == "105376")
    assert q["title"] == "Agent RA (edited)"
    assert [c["text"] for c in q["constraints"]] == ["1 <= |s| <= 50", "s has only a, b, ?"]
    # bad edits are rejected without touching the stored question
    r = client.post(f"/jobs/{job_id}/q/105376/edit", data={"path": "id", "value": "hacked"})
    assert "cannot be edited" in r.text
    r = client.post(f"/jobs/{job_id}/q/105376/edit", data={"path": "time_limit_seconds", "value": "fast"})
    assert r.status_code == 200 and "is not a number" in r.text
    assert json.loads(client.get(f"/jobs/{job_id}/export.json").content)[0]["id"] == "105376"


def test_edit_helper_paths():
    from auditcodes.ingest import extract

    q = extract(FIXTURE).questions[0]
    q2 = apply_edit(q, "hidden_tests.9.stdin", "?b?b\r\n")
    assert q2.hidden_tests[9].stdin == "?b?b\n" and q.hidden_tests[9].stdin != "?b?b\n"
    q3 = apply_edit(q, "solutions.java", "class Main {}")
    assert q3.solutions[Language.JAVA] == "class Main {}"
    with pytest.raises(EditError):
        apply_edit(q, "hidden_tests.99.stdin", "x")
    with pytest.raises(EditError):
        apply_edit(q, "provenance.warnings", "x")
    assert apply_edit(q, "time_limit_seconds", " ").time_limit_seconds is None


def test_verify_runs_editorial_in_background(client, job_id):
    require_language(Language.JAVA)
    r = client.post(f"/jobs/{job_id}/q/105376/verify", data={"language": "java", "normalize": "1"})
    assert r.status_code == 200 and "hx-trigger" in r.text
    url = r.text.split('hx-get="')[1].split('"')[0]
    deadline = time.time() + 120
    while time.time() < deadline:
        r = client.get(url)
        if "hx-trigger" not in r.text:
            break
        time.sleep(0.5)
    assert "13/13 passed" in r.text, r.text[-2000:]
    assert "normalised: 16 typographic single quote" in r.text
    # the result is persisted and shown on the question page
    assert "13/13 passed" in client.get(f"/jobs/{job_id}/q/105376").text
    # without normalisation the faithful extraction fails to build
    r = client.post(f"/jobs/{job_id}/q/105376/verify", data={"language": "java"})
    url = r.text.split('hx-get="')[1].split('"')[0]
    while "hx-trigger" in (r := client.get(url)).text:
        time.sleep(0.5)
    assert "build failed" in r.text and "u2019" in r.text


def test_delete_job(client, job_id):
    r = client.post(f"/jobs/{job_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/jobs/{job_id}").status_code == 404


# --- audit flows -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audit_job(client):
    r = client.post("/jobs", files={"file": ("coding_ques_sample.pdf", FIXTURE.read_bytes(), "application/pdf")}, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[1]


def _wait(client, url, needle="hx-trigger", timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(url)
        if needle not in r.text:
            return r
        time.sleep(0.5)
    raise AssertionError("task did not finish")


def test_audit_question_and_accept_patch(client, audit_job):
    require_language(Language.JAVA)
    r = client.post(f"/jobs/{audit_job}/q/105376/audit")
    assert r.status_code == 200 and "hx-trigger" in r.text
    url = r.text.split('hx-get="')[1].split('"')[0]
    r = _wait(client, url)
    assert "SOL-002" in r.text and "HIDE-001" in r.text and "verified by execution" in r.text
    assert "1 blocker" in r.text and "1 major" in r.text
    page = client.get(f"/jobs/{audit_job}/q/105376").text
    assert "SOL-002" in page and "Re-run audit" in page
    # job overview shows the counts
    assert "1 blocker" in client.get(f"/jobs/{audit_job}").text
    # accept the remove-duplicate patch
    fid = page.split("HIDE-001")[0].rsplit("findings/", 1)[-1] if "findings/" in page.split("HIDE-001")[0] else None
    import re

    ids = re.findall(r'findings/([0-9a-f]+)/accept', page)
    report = json.loads((Path(client.app.state.store.root) / audit_job / "audit" / "105376.json").read_text())
    dup_id = next(f["id"] for f in report["findings"] if f["rule_id"] == "HIDE-001")
    assert dup_id in ids
    r = client.post(f"/jobs/{audit_job}/q/105376/findings/{dup_id}/accept")
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    q = next(x for x in json.loads(client.get(f"/jobs/{audit_job}/export.json").content) if x["id"] == "105376")
    assert len(q["hidden_tests"]) == 9
    assert "accepted · applied" in client.get(f"/jobs/{audit_job}/q/105376").text
    # accepting again fails cleanly (the item is gone)
    r = client.post(f"/jobs/{audit_job}/q/105376/findings/{dup_id}/accept")
    assert "no longer present" in r.text or "accepted" in r.text
    # reject / waive / reopen on the code patch finding
    sol_id = next(f["id"] for f in report["findings"] if f["rule_id"] == "SOL-002")
    assert "rejected" in client.post(f"/jobs/{audit_job}/q/105376/findings/{sol_id}/reject").text
    assert "Reopen" in client.get(f"/jobs/{audit_job}/q/105376").text
    assert "Accept" in client.post(f"/jobs/{audit_job}/q/105376/findings/{sol_id}/reopen").text
    assert client.post(f"/jobs/{audit_job}/q/105376/findings/nope/waive").status_code == 404


def test_audit_all(client, audit_job):
    require_language(Language.JAVA)
    r = client.post(f"/jobs/{audit_job}/audit")
    assert "hx-trigger" in r.text
    url = r.text.split('hx-get="')[1].split('"')[0]
    r = _wait(client, url)
    assert "Audit finished for 2" in r.text
    overview = client.get(f"/jobs/{audit_job}").text
    assert "3 major" in overview  # question 2: HIDE-001, HIDE-002, HIDE-003


def test_accept_verified_applies_everything(client, audit_job):
    require_language(Language.JAVA)
    # fresh job so the earlier accept/reject clicks do not interfere
    r = client.post("/jobs", files={"file": ("coding_ques_sample.pdf", FIXTURE.read_bytes(), "application/pdf")}, follow_redirects=False)
    job = r.headers["location"].rsplit("/", 1)[1]
    r = client.post(f"/jobs/{job}/q/105376/audit")
    url = r.text.split('hx-get="')[1].split('"')[0]
    r = _wait(client, url)
    assert "If you accept all" in r.text and "Accept all verified patches" in r.text
    r = client.post(f"/jobs/{job}/q/105376/findings/accept-verified")
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    q = next(x for x in json.loads(client.get(f"/jobs/{job}/export.json").content) if x["id"] == "105376")
    assert len(q["hidden_tests"]) == 9 and "’" not in q["solutions"]["java"]
    page = client.get(f"/jobs/{job}/q/105376").text
    assert page.count("accepted · applied") == 2 and "If you accept all" not in page
