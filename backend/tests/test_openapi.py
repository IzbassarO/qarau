from fastapi.testclient import TestClient

from app.main import app

NEW_PATHS = [
    "/api/organizations",
    "/api/sites",
    "/api/inspections",
    "/api/inspections/{inspection_id}",
    "/api/inspections/{inspection_id}/runs",
    "/api/inspections/{inspection_id}/runs/{run_id}/steps/{step}/rerun",
    "/api/inspections/{inspection_id}/video",
    "/api/inspections/{inspection_id}/video/chunks",
    "/api/inspections/{inspection_id}/video/complete",
    "/api/inspections/{inspection_id}/findings",
    "/api/findings/{finding_id}",
    "/api/findings/{finding_id}/decisions",
    "/api/users",
    "/api/audit-log",
    "/api/audit-log/{entry_id}",
    "/api/decisions/{decision_id}",
]

EXPECTED_OPERATIONS = {
    "/api/organizations": {"get", "post"},
    "/api/sites": {"get", "post"},
    "/api/inspections": {"get", "post"},
    "/api/inspections/{inspection_id}": {"get"},
    "/api/inspections/{inspection_id}/runs": {"get", "post"},
    "/api/inspections/{inspection_id}/runs/{run_id}/steps/{step}/rerun": {"post"},
    "/api/inspections/{inspection_id}/video": {"post"},
    "/api/inspections/{inspection_id}/video/chunks": {"get", "put"},
    "/api/inspections/{inspection_id}/video/complete": {"post"},
    "/api/inspections/{inspection_id}/findings": {"get", "post"},
    "/api/findings/{finding_id}": {"get"},
    "/api/findings/{finding_id}/decisions": {"get", "post"},
    "/api/users": {"get", "post"},
    "/api/audit-log": {"get"},
    "/api/audit-log/{entry_id}": {"put", "patch", "delete"},
    "/api/decisions/{decision_id}": {"put", "patch", "delete"},
}


def test_openapi_lists_every_new_route_with_a_role() -> None:
    spec = TestClient(app).get("/openapi.json").json()
    paths = spec["paths"]
    for path in NEW_PATHS:
        assert path in paths, path
        assert set(paths[path]) == EXPECTED_OPERATIONS[path], path
    # Every operation except the health check names its role.
    for path, ops in paths.items():
        if path == "/health":
            continue
        for method, op in ops.items():
            assert "Role:" in op.get("description", ""), f"{method.upper()} {path}"
    # The inspection status carries the step list in the shape the web app parses.
    step = spec["components"]["schemas"]["StepOut"]["properties"]
    assert set(step) == {"name", "status", "started_at", "finished_at", "duration_s", "error"}
    insp = spec["components"]["schemas"]["InspectionStatus"]["properties"]
    assert {"status", "run_id", "progress", "current_step", "error", "steps"} <= set(insp)


def test_openapi_decision_operations_declare_their_roles() -> None:
    spec = TestClient(app).get("/openapi.json").json()
    ops = spec["paths"]["/api/findings/{finding_id}/decisions"]
    assert ops["post"]["description"].startswith("Role: engineer.")
    assert ops["get"]["description"].startswith("Role: viewer.")
    assert "201" in ops["post"]["responses"]
    body = spec["components"]["schemas"]["DecisionIn"]["properties"]
    assert set(body) == {"verdict", "comment", "new_class", "new_severity"}
    out = spec["components"]["schemas"]["DecisionOut"]["properties"]
    assert {"user", "verdict", "comment", "new_class", "new_severity", "created_at"} <= set(out)
    assert spec["components"]["schemas"]["Verdict"]["enum"] == ["confirm", "reject", "reclass"]
