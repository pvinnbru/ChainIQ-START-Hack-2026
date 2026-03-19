from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


# ── User ──────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    business_unit: Optional[str] = None
    country: Optional[str] = None
    site: Optional[str] = None
    requester_role: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    user_id: str


# ── Escalation ────────────────────────────────────────────────────────────────

class EscalationOut(BaseModel):
    id: str
    request_id: str
    type: str
    target_user_id: Optional[str] = None
    status: str
    message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EscalationCreate(BaseModel):
    request_id: str
    type: str  # requester_clarification | procurement_manager | category_head | compliance
    message: Optional[str] = None


# ── Request ───────────────────────────────────────────────────────────────────

class RequestCreate(BaseModel):
    plain_text: str
    title: Optional[str] = None
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    currency: Optional[str] = None
    budget_amount: Optional[float] = None
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    required_by_date: Optional[str] = None
    preferred_supplier_mentioned: Optional[str] = None
    incumbent_supplier: Optional[str] = None
    contract_type_requested: Optional[str] = None
    delivery_countries: Optional[List[str]] = None
    data_residency_constraint: bool = False
    esg_requirement: bool = False


class RequestOut(BaseModel):
    id: str
    requester_id: str
    plain_text: str
    status: str
    title: Optional[str] = None
    business_unit: Optional[str] = None
    country: Optional[str] = None
    site: Optional[str] = None
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    currency: Optional[str] = None
    budget_amount: Optional[float] = None
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    required_by_date: Optional[str] = None
    preferred_supplier_mentioned: Optional[str] = None
    incumbent_supplier: Optional[str] = None
    contract_type_requested: Optional[str] = None
    delivery_countries: Optional[str] = None  # JSON string
    data_residency_constraint: bool = False
    esg_requirement: bool = False
    ai_output: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    escalations: List[EscalationOut] = []

    model_config = {"from_attributes": True}


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditEntryOut(BaseModel):
    id: str
    request_id: str
    actor_id: str
    action: str
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Action with optional notes ─────────────────────────────────────────────────

class ActionRequest(BaseModel):
    notes: Optional[str] = None


# ── Clarification ─────────────────────────────────────────────────────────────

class ClarificationCreate(BaseModel):
    fields: dict
    notes: Optional[str] = None
