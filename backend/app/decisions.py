"""Decision log (REV1-api, ADR 0025): an engineer confirms, rejects or reclassifies a finding.

POST /api/findings/{id}/decisions  (engineer) -> 201 DecisionOut (insert only, one AuditLog row)
GET  /api/findings/{id}/decisions  (viewer)   -> [DecisionOut], oldest first

No PUT, PATCH or DELETE handler exists (FastAPI answers 405) and the database trigger refuses
UPDATE and DELETE on `decision` (migration 0001; since 0007 only `app.event purge` may delete
a guest's decision in an event organization, ADR 0034). A guest decides only on its own
findings. The finding's effective status, class and severity are replayed from its decisions
(`derive_finding_state`); nothing is written back.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from qarau_pipeline.report.inputs import replay_decisions
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Identity
from .crud import Db, EngineerOrGuest, ViewerOrGuest, audit
from .models import Decision, Finding, FindingClass, Severity, User, Verdict

router = APIRouter(prefix="/api")


class DecisionIn(BaseModel):
    verdict: Verdict
    comment: str | None = Field(default=None, max_length=2000)
    new_class: FindingClass | None = None
    new_severity: Severity | None = None

    @model_validator(mode="after")
    def consistent(self) -> "DecisionIn":
        if self.verdict != Verdict.confirm and not (self.comment or "").strip():
            raise ValueError(f"{self.verdict.value} needs a comment")
        carries_new = self.new_class is not None or self.new_severity is not None
        if self.verdict == Verdict.reclass and not carries_new:
            raise ValueError("reclass needs new_class and/or new_severity")
        if self.verdict != Verdict.reclass and carries_new:
            raise ValueError("only reclass carries new_class / new_severity")
        return self


class DecisionOut(BaseModel):
    id: int
    finding_id: int
    user_id: int
    user: str
    verdict: Verdict
    comment: str | None
    new_class: FindingClass | None
    new_severity: Severity | None
    created_at: datetime


def decisions_of(db: Session, finding_id: int) -> list[tuple[Decision, str]]:
    """(decision, user name) of a finding, oldest first."""
    rows = db.execute(
        select(Decision, User.name)
        .join(User, Decision.user_id == User.id)
        .where(Decision.finding_id == finding_id)
        .order_by(Decision.created_at, Decision.id)
    ).all()
    return [(d, name) for d, name in rows]


def derive_finding_state(
    finding: Finding, decisions: Sequence[Decision]
) -> tuple[str, str, Severity]:
    """(status, class, severity) after replaying `decisions` oldest first. The status is that of
    the latest decision: reject -> rejected; confirm and reclass -> confirmed. A reclass sets the
    class and/or severity it carries (a field it does not carry keeps its previous value) and
    later decisions keep them. No decision: the proposed values stored on the finding.
    The rule itself lives in qarau_pipeline.report.inputs, so the PDF report replays the same."""
    status, class_, severity = replay_decisions(
        finding.status, finding.class_, finding.severity, decisions
    )
    return status, class_, Severity(severity)


def state_of(db: Session, finding: Finding) -> tuple[str, str, Severity]:
    """`derive_finding_state` over the finding's stored decisions."""
    rows = db.scalars(
        select(Decision)
        .where(Decision.finding_id == finding.id)
        .order_by(Decision.created_at, Decision.id)
    ).all()
    return derive_finding_state(finding, rows)


def to_out(decision: Decision, user: str) -> DecisionOut:
    return DecisionOut(
        id=decision.id,
        finding_id=decision.finding_id,
        user_id=decision.user_id,
        user=user,
        verdict=decision.verdict,
        comment=decision.comment,
        new_class=None if decision.new_class is None else FindingClass(decision.new_class),
        new_severity=decision.new_severity,
        created_at=decision.created_at,
    )


def finding_or_404(db: Session, finding_id: int, who: Identity) -> Finding:
    """The finding if it is in the caller's sandbox: a guest's own finding for that guest, a
    finding of no guest for everyone else (ADR 0034); else 404, never 403, so ids leak nothing."""
    finding = db.get(Finding, finding_id)
    if finding is None or finding.guest_id != who.guest_id:
        raise HTTPException(404, "finding not found")
    return finding


@router.post(
    "/findings/{finding_id}/decisions",
    status_code=201,
    description="Role: engineer. Record a decision on a finding: verdict confirm | reject | "
    "reclass; comment is required for reject and reclass; reclass carries new_class and/or "
    "new_severity (confirm and reject may not). The user comes from the session and created_at "
    "from the server. Insert only: the same body twice makes two rows; nothing is ever edited. "
    "Also a guest, on its own findings only (another's: 404); the decision carries its guest id.",
)
def create_decision(
    finding_id: int, body: DecisionIn, db: Db, who: EngineerOrGuest, request: Request
) -> DecisionOut:
    finding_or_404(db, finding_id, who)
    if who.user_id is None:  # the shared token is read-only; never reached for a write
        raise HTTPException(403, "writes need a user token")
    # Insert only, no dedup: the same body twice is two rows (both are evidence of a decision).
    decision = Decision(
        finding_id=finding_id,
        user_id=who.user_id,
        verdict=body.verdict,
        comment=body.comment,
        new_class=None if body.new_class is None else body.new_class.value,
        new_severity=body.new_severity,
        guest_id=who.guest_id,
    )
    db.add(decision)
    db.flush()
    # the route carries the finding id, object_id the decision id
    audit(db, who, request, "decision", decision.id, "finding.decision")
    db.commit()
    user = db.get(User, who.user_id)
    return to_out(decision, user.name if user is not None else "")


@router.get(
    "/findings/{finding_id}/decisions",
    description="Role: viewer. The full decision history of a finding, oldest first "
    "(user, verdict, comment, new class / severity, created_at). A guest: its own findings only.",
)
def list_decisions(finding_id: int, db: Db, who: ViewerOrGuest) -> list[DecisionOut]:
    finding_or_404(db, finding_id, who)
    return [to_out(d, user) for d, user in decisions_of(db, finding_id)]
