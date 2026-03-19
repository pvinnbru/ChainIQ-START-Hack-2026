import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc, func
from datetime import datetime
from database import get_db
from auth import get_current_user
import models
import schemas

router = APIRouter(prefix="/requests", tags=["requests"])


def _order_column(sort_by: str, order: str):
    col_map = {
        "date": models.Request.created_at,
        "l1": models.Request.category_l1,
        "l2": models.Request.category_l2,
        "country": models.Request.country,
    }
    col = col_map.get(sort_by, models.Request.created_at)
    return asc(col) if order == "asc" else desc(col)


def _add_audit(db: Session, request_id: str, actor_id: str, action: str, notes: str | None = None):
    db.add(models.AuditEntry(
        request_id=request_id,
        actor_id=actor_id,
        action=action,
        notes=notes,
    ))


@router.get("", response_model=list[schemas.RequestOut])
def list_requests(
    sort_by: str = Query("date", pattern="^(date|l1|l2|country)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    offset = (page - 1) * per_page
    return (
        db.query(models.Request)
        .order_by(_order_column(sort_by, order))
        .offset(offset)
        .limit(per_page)
        .all()
    )


@router.get("/count")
def count_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return {"total": db.query(models.Request).count()}


@router.get("/stats")
def request_stats(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(models.Request)
    if current_user.role == "requester":
        query = query.filter(models.Request.requester_id == current_user.id)
    rows = (
        query.with_entities(models.Request.status, func.count(models.Request.id))
        .group_by(models.Request.status)
        .all()
    )
    by_status = {status: count for status, count in rows}
    return {"by_status": by_status, "total": sum(by_status.values())}


@router.get("/activity")
def recent_activity(
    limit: int = Query(10, ge=1, le=50),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entries = (
        db.query(models.AuditEntry, models.Request, models.User)
        .join(models.Request, models.AuditEntry.request_id == models.Request.id)
        .join(models.User, models.AuditEntry.actor_id == models.User.id)
        .order_by(desc(models.AuditEntry.created_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "id": entry.id,
            "action": entry.action,
            "notes": entry.notes,
            "created_at": entry.created_at.isoformat(),
            "actor_name": user.name,
            "request_id": request.id,
            "request_title": request.title or request.plain_text[:60],
        }
        for entry, request, user in entries
    ]


@router.get("/mine", response_model=list[schemas.RequestOut])
def my_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.Request)
        .filter(models.Request.requester_id == current_user.id)
        .order_by(asc(models.Request.created_at))
        .all()
    )


@router.post("", response_model=schemas.RequestOut)
def create_request(
    body: schemas.RequestCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delivery_countries_json = json.dumps(body.delivery_countries) if body.delivery_countries else None

    req = models.Request(
        requester_id=current_user.id,
        plain_text=body.plain_text,
        status="new",
        title=body.title,
        business_unit=current_user.business_unit,
        country=current_user.country,
        site=current_user.site,
        category_l1=body.category_l1,
        category_l2=body.category_l2,
        currency=body.currency,
        budget_amount=body.budget_amount,
        quantity=body.quantity,
        unit_of_measure=body.unit_of_measure,
        required_by_date=body.required_by_date,
        preferred_supplier_mentioned=body.preferred_supplier_mentioned,
        incumbent_supplier=body.incumbent_supplier,
        contract_type_requested=body.contract_type_requested,
        delivery_countries=delivery_countries_json,
        data_residency_constraint=body.data_residency_constraint,
        esg_requirement=body.esg_requirement,
    )
    db.add(req)
    db.flush()  # get req.id before audit entry
    _add_audit(db, req.id, current_user.id, "submitted")
    db.commit()
    db.refresh(req)
    return req


@router.get("/{request_id}", response_model=schemas.RequestOut)
def get_request(
    request_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


@router.get("/{request_id}/audit", response_model=list[schemas.AuditEntryOut])
def get_audit_trail(
    request_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.AuditEntry)
        .filter(models.AuditEntry.request_id == request_id)
        .order_by(asc(models.AuditEntry.created_at))
        .all()
    )


@router.post("/{request_id}/clarify", response_model=schemas.RequestOut)
def clarify_request(
    request_id: str,
    body: schemas.ClarificationCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.requester_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your request")

    allowed_fields = {
        "title", "category_l1", "category_l2", "currency", "budget_amount",
        "quantity", "unit_of_measure", "required_by_date",
        "preferred_supplier_mentioned", "incumbent_supplier",
    }
    for field, value in body.fields.items():
        if field in allowed_fields and value is not None:
            setattr(req, field, value)

    db.add(models.Clarification(
        request_id=request_id,
        submitted_fields=json.dumps(body.fields),
    ))
    db.query(models.Escalation).filter(
        models.Escalation.request_id == request_id,
        models.Escalation.type == "requester_clarification",
        models.Escalation.status == "pending",
    ).update({"status": "resolved"})

    req.status = "pending_review"
    req.updated_at = datetime.utcnow()
    _add_audit(db, request_id, current_user.id, "clarified", body.notes)
    db.commit()
    db.refresh(req)
    return req


@router.post("/{request_id}/withdraw", response_model=schemas.RequestOut)
def withdraw_request(
    request_id: str,
    body: schemas.ActionRequest = schemas.ActionRequest(),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.requester_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your request")
    if req.status in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Cannot withdraw a finalised request")
    req.status = "withdrawn"
    req.updated_at = datetime.utcnow()
    _add_audit(db, request_id, current_user.id, "withdrawn", body.notes)
    db.commit()
    db.refresh(req)
    return req


@router.post("/{request_id}/review", response_model=schemas.RequestOut)
def review_request(
    request_id: str,
    body: schemas.ActionRequest = schemas.ActionRequest(),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in ("approver", "category_head", "compliance_reviewer"):
        raise HTTPException(status_code=403, detail="Not authorised to review requests")
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.status = "reviewed"
    req.updated_at = datetime.utcnow()
    db.query(models.Escalation).filter(
        models.Escalation.request_id == request_id,
        models.Escalation.target_user_id == current_user.id,
        models.Escalation.status == "pending",
    ).update({"status": "resolved"})
    _add_audit(db, request_id, current_user.id, "reviewed", body.notes)
    db.commit()
    db.refresh(req)
    return req


@router.post("/{request_id}/approve", response_model=schemas.RequestOut)
def approve_request(
    request_id: str,
    body: schemas.ActionRequest = schemas.ActionRequest(),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in ("approver", "category_head", "compliance_reviewer"):
        raise HTTPException(status_code=403, detail="Not authorised to approve requests")
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.status = "approved"
    req.updated_at = datetime.utcnow()
    db.query(models.Escalation).filter(
        models.Escalation.request_id == request_id,
        models.Escalation.target_user_id == current_user.id,
        models.Escalation.status == "pending",
    ).update({"status": "resolved"})
    _add_audit(db, request_id, current_user.id, "approved", body.notes)
    db.commit()
    db.refresh(req)
    return req


@router.post("/{request_id}/reject", response_model=schemas.RequestOut)
def reject_request(
    request_id: str,
    body: schemas.ActionRequest = schemas.ActionRequest(),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in ("approver", "category_head", "compliance_reviewer"):
        raise HTTPException(status_code=403, detail="Not authorised to reject requests")
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    req.status = "rejected"
    req.updated_at = datetime.utcnow()
    db.query(models.Escalation).filter(
        models.Escalation.request_id == request_id,
        models.Escalation.target_user_id == current_user.id,
        models.Escalation.status == "pending",
    ).update({"status": "resolved"})
    _add_audit(db, request_id, current_user.id, "rejected", body.notes)
    db.commit()
    db.refresh(req)
    return req
