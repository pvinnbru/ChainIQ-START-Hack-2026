import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey, Text, Integer
from sqlalchemy.orm import relationship
from database import Base


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    role = Column(String, nullable=False)  # requester | approver | category_head | compliance_reviewer
    business_unit = Column(String, nullable=True)
    country = Column(String, nullable=True)
    site = Column(String, nullable=True)
    requester_role = Column(String, nullable=True)  # job title

    requests = relationship("Request", back_populates="requester")
    escalations = relationship("Escalation", back_populates="target_user")


class Request(Base):
    __tablename__ = "requests"

    id = Column(String, primary_key=True, default=gen_uuid)
    requester_id = Column(String, ForeignKey("users.id"), nullable=False)
    plain_text = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="new")
    title = Column(String, nullable=True)
    business_unit = Column(String, nullable=True)
    country = Column(String, nullable=True)
    site = Column(String, nullable=True)
    category_l1 = Column(String, nullable=True)
    category_l2 = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    budget_amount = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    unit_of_measure = Column(String, nullable=True)
    required_by_date = Column(String, nullable=True)
    preferred_supplier_mentioned = Column(String, nullable=True)
    incumbent_supplier = Column(String, nullable=True)
    contract_type_requested = Column(String, nullable=True)
    delivery_countries = Column(Text, nullable=True)  # JSON string
    data_residency_constraint = Column(Boolean, default=False)
    esg_requirement = Column(Boolean, default=False)
    ai_output = Column(Text, nullable=True)  # JSON string, set by AI step
    execution_log_id = Column(String, nullable=True)  # e.g. "REQ-000004" → stores/execution_logs/{id}.json
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requester = relationship("User", back_populates="requests")
    escalations = relationship("Escalation", back_populates="request")
    clarifications = relationship("Clarification", back_populates="request")


class Escalation(Base):
    __tablename__ = "escalations"

    id = Column(String, primary_key=True, default=gen_uuid)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    type = Column(String, nullable=False)  # requester_clarification | procurement_manager | category_head | compliance
    target_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | resolved
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    request = relationship("Request", back_populates="escalations")
    target_user = relationship("User", back_populates="escalations")


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    actor_id = Column(String, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)  # submitted | escalated | clarified | reviewed | approved | rejected | withdrawn
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Clarification(Base):
    __tablename__ = "clarifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    request_id = Column(String, ForeignKey("requests.id"), nullable=False)
    submitted_fields = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, default=datetime.utcnow)

    request = relationship("Request", back_populates="clarifications")
