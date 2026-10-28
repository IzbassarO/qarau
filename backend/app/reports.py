"""PDF inspection report (RPT1, ADR 0027).

GET /api/inspections/{id}/report.pdf?lang=ru|kk|en  (viewer) -> application/pdf

The route and `python -m qarau_pipeline report` call the same `qarau_pipeline.report.generate`
(loader + renderer), so the same data, language and report time give byte-identical files.
Every generation inserts a `report` row with the file's SHA-256 and the report time, which the
response also carries as X-Report-SHA256 and X-Report-Generated-At.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from qarau_pipeline.report import generate

from .crud import Db, Viewer, get_or_404
from .db import engine_for
from .findings import Cfg
from .models import Inspection, Report

router = APIRouter(prefix="/api")


def report_clock() -> datetime:
    """The report time: now in the server's zone, whole seconds (the PDF prints minutes; tests
    override this dependency to fix it)."""
    return datetime.now().astimezone().replace(microsecond=0)


Clock = Annotated[datetime, Depends(report_clock)]


@router.get(
    "/inspections/{inspection_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    description="Role: viewer. The inspection report as PDF/A in `lang` (ru, kk or en): cover, "
    "executive summary, map, findings by severity, one card per finding with its evidence "
    "frames and decision log, not inspected areas, method and limitations, sign-off, document "
    "control. Headers X-Report-SHA256 (of the body) and X-Report-Generated-At (the report time "
    "printed in the PDF); each call is logged in the report table. 404: unknown inspection.",
)
def get_report_pdf(
    inspection_id: int, lang: Literal["ru", "kk", "en"], db: Db, who: Viewer, settings: Cfg,
    now: Clock,
) -> Response:  # fmt: skip
    get_or_404(db, Inspection, inspection_id)
    engine = engine_for(settings.database_url)
    pdf, sha, inp = generate(inspection_id, lang, now, engine, Path(settings.qarau_runs_dir))
    db.add(
        Report(
            inspection_id=inspection_id, lang=lang, sha256=sha, generated_at=now.astimezone(UTC),
            report_number=inp.report_number, version=inp.version, created_by=who.user_id,
        )
    )  # fmt: skip
    db.commit()
    name = f"{inp.report_number}-v{inp.version}-{lang}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            "X-Report-SHA256": sha,
            "X-Report-Generated-At": now.isoformat(),
        },
    )
