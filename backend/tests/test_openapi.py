from fastapi.testclient import TestClient

from app.main import app

NEW_PATHS = [
    "/api/organizations",
    "/api/sites",
    "/api/inspections",
    "/api/inspections/{inspection_id}",
    "/api/inspections/{inspection_id}/runs",
    "/api/inspections/{inspection_id}/video",
    "/api/inspections/{inspection_id}/video/chunks",
    "/api/inspections/{inspection_id}/video/complete",
    "/api/inspections/{inspection_id}/findings",
    "/api/findings/{finding_id}",
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
    "/api/inspections/{inspection_id}/runs": {"get"},
    "/api/inspections/{inspection_id}/video": {"post"},
    "/api/inspections/{inspection_id}/video/chunks": {"get", "put"},
    "/api/inspections/{inspection_id}/video/complete": {"post"},
    "/api/inspections/{inspection_id}/findings": {"get", "post"},
    "/api/findings/{finding_id}": {"get"},
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
