"""Run once to populate the database with demo users and sample requests."""
import uuid
import json
from datetime import datetime, timedelta
from database import engine, SessionLocal, Base
import models  # noqa: F401

Base.metadata.create_all(bind=engine)

db = SessionLocal()

# ── Demo users ────────────────────────────────────────────────────────────────
USERS = [
    {
        "id": "user-alice",
        "name": "Alice Müller",
        "email": "alice@chainiq.demo",
        "role": "requester",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "requester_role": "Workplace Lead",
    },
    {
        "id": "user-bob",
        "name": "Bob Schmidt",
        "email": "bob@chainiq.demo",
        "role": "approver",
        "business_unit": "Procurement",
        "country": "DE",
        "site": "Munich",
        "requester_role": "Procurement Manager",
    },
    {
        "id": "user-carol",
        "name": "Carol Dupont",
        "email": "carol@chainiq.demo",
        "role": "category_head",
        "business_unit": "IT",
        "country": "FR",
        "site": "Paris",
        "requester_role": "Head of IT Category",
    },
    {
        "id": "user-dave",
        "name": "Dave Patel",
        "email": "dave@chainiq.demo",
        "role": "compliance_reviewer",
        "business_unit": "Legal & Compliance",
        "country": "GB",
        "site": "London",
        "requester_role": "Compliance Reviewer",
    },
]

# ── Sample requests ───────────────────────────────────────────────────────────
SAMPLE_REQUESTS = [
    {
        "id": "req-sample-1",
        "requester_id": "user-alice",
        "plain_text": "Need 500 laptops for new hires joining next month. Prefer Dell if available.",
        "status": "new",
        "title": "Laptop procurement for Q2 onboarding",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Hardware",
        "category_l2": "End-User Devices",
        "currency": "CHF",
        "budget_amount": 750000,
        "quantity": 500,
        "unit_of_measure": "units",
        "required_by_date": "2026-05-01",
        "preferred_supplier_mentioned": "Dell",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["CH"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": datetime.utcnow() - timedelta(days=5),
    },
    {
        "id": "req-sample-2",
        "requester_id": "user-alice",
        "plain_text": "We need IT consulting support for a cloud migration project, roughly 200 days over 6 months.",
        "status": "pending_review",
        "title": "Cloud migration consulting",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Professional Services",
        "category_l2": "IT Project Management Services",
        "currency": "EUR",
        "budget_amount": 200000,
        "quantity": 200,
        "unit_of_measure": "consulting_day",
        "required_by_date": "2026-06-30",
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["CH", "DE"]),
        "data_residency_constraint": True,
        "esg_requirement": False,
        "created_at": datetime.utcnow() - timedelta(days=3),
    },
    {
        "id": "req-sample-3",
        "requester_id": "user-alice",
        "plain_text": "Urgent: 50 monitors needed by next week for a new office setup in Berlin.",
        "status": "escalated",
        "title": "Monitors for Berlin office",
        "business_unit": "Digital Workplace",
        "country": "DE",
        "site": "Berlin",
        "category_l1": "Hardware",
        "category_l2": "End-User Devices",
        "currency": "EUR",
        "budget_amount": 25000,
        "quantity": 50,
        "unit_of_measure": "units",
        "required_by_date": "2026-03-26",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["DE"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": datetime.utcnow() - timedelta(days=1),
    },
]


def seed():
    # Clear existing data
    db.query(models.Clarification).delete()
    db.query(models.Escalation).delete()
    db.query(models.Request).delete()
    db.query(models.User).delete()
    db.commit()

    # Insert users
    for u in USERS:
        db.add(models.User(**u))
    db.commit()

    # Insert sample requests
    for r in SAMPLE_REQUESTS:
        db.add(models.Request(**r))
    db.commit()

    # Add a sample escalation for req-sample-3
    db.add(models.Escalation(
        id="esc-sample-1",
        request_id="req-sample-3",
        type="requester_clarification",
        target_user_id="user-alice",
        status="pending",
        message="Lead time is infeasible — all suppliers need 10+ days. Please confirm if the deadline can be extended or if you can accept partial delivery.",
    ))
    db.commit()

    print("✅ Database seeded successfully.")
    print("Demo users:")
    for u in USERS:
        print(f"  {u['id']:20s}  {u['name']:20s}  [{u['role']}]")


if __name__ == "__main__":
    seed()
    db.close()
