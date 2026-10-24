"""Decision log (REV1-api): POST / GET /api/findings/{id}/decisions on a manual finding made on
the fixture bundle of test_findings_api.py (finding 1: corrosion, watch, proposed)."""

from typing import Any

import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_findings_api import BODY, ENG, VIEW, marked  # noqa: F401  (fixture)

from app.db import engine_for

URL = "/api/findings/1/decisions"
CONFIRM: dict[str, Any] = {"verdict": "confirm"}
REJECT: dict[str, Any] = {"verdict": "reject", "comment": "shadow, not corrosion"}
RECLASS_SEV: dict[str, Any] = {
    "verdict": "reclass",
    "comment": "wall thinning visible",
    "new_severity": "critical",
}


@pytest.fixture
def finding(marked: TestClient) -> TestClient:  # noqa: F811
    assert marked.post("/api/inspections/1/findings", json=BODY, headers=ENG).status_code == 201
    return marked


def history(c: TestClient) -> list[dict[str, Any]]:
    r = c.get(URL, headers=VIEW)
    assert r.status_code == 200, r.text
    rows: list[dict[str, Any]] = r.json()
    return rows


def audit_rows(c: TestClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = c.get("/api/audit-log", headers=bearer("admin")).json()
    return rows


def test_viewer_post_is_403(finding: TestClient) -> None:
    assert finding.post(URL, json=CONFIRM, headers=VIEW).status_code == 403
    assert finding.post(URL, json=CONFIRM).status_code == 401
    assert history(finding) == []


def test_confirm_then_reject_gives_history_of_2_and_status_rejected(finding: TestClient) -> None:
    r = finding.post(URL, json=CONFIRM, headers=ENG)
    assert r.status_code == 201, r.text
    first = r.json()
    assert (first["id"], first["finding_id"], first["user_id"], first["user"]) == (
        1,
        1,
        2,
        "engineer",
    )
    assert (first["verdict"], first["comment"], first["new_class"], first["new_severity"]) == (
        "confirm", None, None, None
    )  # fmt: skip
    assert first["created_at"]
    f = finding.get("/api/findings/1", headers=VIEW).json()
    assert (f["status"], f["class"], f["severity"]) == ("confirmed", "corrosion", "watch")

    assert finding.post(URL, json=REJECT, headers=ENG).status_code == 201
    rows = history(finding)
    assert len(rows) == 2
    assert [d["verdict"] for d in rows] == ["confirm", "reject"]
    assert [d["comment"] for d in rows] == [None, "shadow, not corrosion"]
    assert [d["user"] for d in rows] == ["engineer", "engineer"]
    assert rows[0]["created_at"] <= rows[1]["created_at"]
    f = finding.get("/api/findings/1", headers=VIEW).json()
    assert (f["status"], f["class"], f["severity"]) == ("rejected", "corrosion", "watch")
    listed = finding.get("/api/inspections/1/findings", headers=VIEW).json()
    assert [x["status"] for x in listed] == ["rejected"]
    # the history is read-only for the viewer and the shared token, never trimmed
    assert history(finding) == rows


def test_reclass_to_critical_changes_the_finding_severity(finding: TestClient) -> None:
    r = finding.post(URL, json=RECLASS_SEV, headers=ENG)
    assert r.status_code == 201, r.text
    assert (r.json()["new_class"], r.json()["new_severity"]) == (None, "critical")
    f = finding.get("/api/findings/1", headers=VIEW).json()
    assert (f["status"], f["class"], f["severity"]) == ("confirmed", "corrosion", "critical")
    # a later reclass of the class alone keeps the critical severity; a confirm keeps both
    body = {"verdict": "reclass", "comment": "it is a crack", "new_class": "crack"}
    assert finding.post(URL, json=body, headers=ENG).status_code == 201
    assert finding.post(URL, json=CONFIRM, headers=ENG).status_code == 201
    f = finding.get("/api/findings/1", headers=VIEW).json()
    assert (f["status"], f["class"], f["severity"]) == ("confirmed", "crack", "critical")
    assert [d["verdict"] for d in history(finding)] == ["reclass", "reclass", "confirm"]


@pytest.mark.parametrize(
    "bad",
    [
        {"verdict": "reject"},  # reject without a comment
        {"verdict": "reject", "comment": "   "},
        {"verdict": "reclass", "new_severity": "critical"},  # reclass without a comment
        {"verdict": "reclass", "comment": "why"},  # reclass without new_class / new_severity
        {"verdict": "confirm", "new_severity": "critical"},  # only reclass carries them
        {"verdict": "reject", "comment": "x", "new_class": "crack"},
        {"verdict": "reclass", "comment": "x", "new_class": "rust"},  # unknown class
        {"verdict": "reclass", "comment": "x", "new_severity": "medium"},
        {"verdict": "approve"},  # unknown verdict
        {},
    ],
)
def test_bad_bodies_are_422_and_store_nothing(finding: TestClient, bad: dict[str, Any]) -> None:
    before = len(audit_rows(finding))
    r = finding.post(URL, json=bad, headers=ENG)
    assert r.status_code == 422, r.text
    assert history(finding) == []
    assert len(audit_rows(finding)) == before


def test_unknown_finding_is_404(finding: TestClient) -> None:
    assert finding.post("/api/findings/99/decisions", json=CONFIRM, headers=ENG).status_code == 404
    assert finding.get("/api/findings/99/decisions", headers=VIEW).status_code == 404


def test_same_post_twice_makes_two_rows(finding: TestClient) -> None:
    assert finding.post(URL, json=REJECT, headers=ENG).status_code == 201
    assert finding.post(URL, json=REJECT, headers=ENG).status_code == 201
    rows = history(finding)
    assert [(d["id"], d["verdict"], d["comment"]) for d in rows] == [
        (1, "reject", "shadow, not corrosion"),
        (2, "reject", "shadow, not corrosion"),
    ]


def test_user_and_time_come_from_the_server(finding: TestClient) -> None:
    body = {**CONFIRM, "user_id": 3, "user": "viewer", "created_at": "2000-01-01T00:00:00Z"}
    r = finding.post(URL, json=body, headers=ENG)
    assert r.status_code == 201, r.text
    assert (r.json()["user_id"], r.json()["user"]) == (2, "engineer")
    assert not r.json()["created_at"].startswith("2000-")


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_put_patch_delete_are_405(finding: TestClient, method: str) -> None:
    assert finding.post(URL, json=CONFIRM, headers=ENG).status_code == 201
    for headers in (ENG, VIEW, bearer("admin")):
        r = finding.request(method, URL, json=REJECT, headers=headers)
        assert r.status_code == 405, (method, r.text)
        r = finding.request(method, f"{URL}/1", json=REJECT, headers=headers)
        assert r.status_code in (404, 405), (method, r.text)  # no such route either
    assert [d["verdict"] for d in history(finding)] == ["confirm"]


def test_each_post_adds_exactly_one_audit_row(finding: TestClient) -> None:
    before = len(audit_rows(finding))
    assert finding.post(URL, json=CONFIRM, headers=ENG).status_code == 201
    assert len(audit_rows(finding)) == before + 1
    assert finding.post(URL, json=REJECT, headers=ENG).status_code == 201
    log = audit_rows(finding)
    assert len(log) == before + 2
    entry = (2, "POST /api/findings/1/decisions", "finding", "1", "decision.create")
    assert [
        (e["user_id"], e["route"], e["object_type"], e["object_id"], e["action"]) for e in log[:2]
    ] == [entry, entry]
    history(finding)  # reads add no audit row
    assert len(audit_rows(finding)) == before + 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE decision SET verdict = 'reject' WHERE id = 1",
        "UPDATE decision SET comment = 'edited'",
        "DELETE FROM decision WHERE id = 1",
        "DELETE FROM decision",
    ],
)
def test_sql_update_and_delete_on_decisions_raise(
    finding: TestClient, db_url: str, sql: str
) -> None:
    assert finding.post(URL, json=CONFIRM, headers=ENG).status_code == 201
    engine = engine_for(db_url)
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(sql))
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, verdict, comment FROM decision")).all()
    assert [tuple(r) for r in rows] == [(1, "confirm", None)]
    assert [d["verdict"] for d in history(finding)] == ["confirm"]
