from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from auth import get_current_user
import models
import schemas


def _add_audit(db: Session, request_id: str, actor_id: str, action: str, notes: str | None = None):
    db.add(models.AuditEntry(
        request_id=request_id,
        actor_id=actor_id,
        action=action,
        notes=notes,
    ))

router = APIRouter(prefix="/escalations", tags=["escalations"])


def _route_escalation(esc_type: str, request: models.Request, db: Session) -> models.User | None:
    """Return the target user for a given escalation type."""
    if esc_type == "requester_clarification":
        return request.requester
    role_map = {
        "procurement_manager": "approver",
        "category_head": "category_head",
        "compliance": "compliance_reviewer",
    }
    target_role = role_map.get(esc_type)
    if not target_role:
        return None
    return db.query(models.User).filter(models.User.role == target_role).first()


@router.get("/me", response_model=list[schemas.EscalationOut])
def my_escalations(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.Escalation)
        .filter(
            models.Escalation.target_user_id == current_user.id,
            models.Escalation.status == "pending",
        )
        .order_by(models.Escalation.created_at.asc())
        .all()
    )


@router.post("", response_model=schemas.EscalationOut)
def create_escalation(
    body: schemas.EscalationCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = db.query(models.Request).filter(models.Request.id == body.request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    target = _route_escalation(body.type, request, db)

    escalation = models.Escalation(
        request_id=body.request_id,
        type=body.type,
        target_user_id=target.id if target else None,
        status="pending",
        message=body.message,
    )
    db.add(escalation)

    request.status = "escalated"
    _add_audit(db, body.request_id, current_user.id, "escalated", body.message)
    db.commit()
    db.refresh(escalation)
    return escalation


@router.post("/{escalation_id}/resolve", response_model=schemas.EscalationOut)
def resolve_escalation(
    escalation_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    escalation = db.query(models.Escalation).filter(models.Escalation.id == escalation_id).first()
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    escalation.status = "resolved"
    db.commit()
    db.refresh(escalation)
    return escalation
