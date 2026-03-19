"""Run once to populate the database with demo users and sample requests."""
import json
from datetime import datetime, timedelta
from database import engine, SessionLocal, Base
import models  # noqa: F401

Base.metadata.create_all(bind=engine)

db = SessionLocal()

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

now = datetime.utcnow()

SAMPLE_REQUESTS = [
    {
        "id": "req-sample-1",
        "requester_id": "user-alice",
        "plain_text": "Need 500 laptops for new hires joining next month. Prefer Dell if available and commercially competitive.",
        "status": "new",
        "title": "Laptop procurement for Q2 onboarding",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "IT",
        "category_l2": "Laptops",
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
        "created_at": now - timedelta(days=7),
        "updated_at": now - timedelta(days=7),
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
        "created_at": now - timedelta(days=5),
        "updated_at": now - timedelta(days=5),
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
        "category_l1": "IT",
        "category_l2": "Monitors",
        "currency": "EUR",
        "budget_amount": 25000,
        "quantity": 50,
        "unit_of_measure": "units",
        "required_by_date": "2026-03-26",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["DE"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": now - timedelta(days=3),
        "updated_at": now - timedelta(days=3),
    },
    {
        "id": "req-sample-4",
        "requester_id": "user-alice",
        "plain_text": "Requesting 10 ergonomic office chairs for the Zürich office to replace aging furniture.",
        "status": "approved",
        "title": "Ergonomic chairs – Zürich office",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Facilities",
        "category_l2": "Office Chairs",
        "currency": "CHF",
        "budget_amount": 8000,
        "quantity": 10,
        "unit_of_measure": "units",
        "required_by_date": "2026-04-15",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["CH"]),
        "data_residency_constraint": False,
        "esg_requirement": True,
        "created_at": now - timedelta(days=14),
        "updated_at": now - timedelta(days=10),
    },
    {
        "id": "req-sample-5",
        "requester_id": "user-alice",
        "plain_text": "Need cybersecurity advisory services for upcoming ISO 27001 audit preparation, approx 30 consulting days.",
        "status": "reviewed",
        "title": "ISO 27001 audit prep consulting",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Professional Services",
        "category_l2": "Cybersecurity Advisory",
        "currency": "CHF",
        "budget_amount": 45000,
        "quantity": 30,
        "unit_of_measure": "consulting_day",
        "required_by_date": "2026-05-30",
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["CH"]),
        "data_residency_constraint": True,
        "esg_requirement": False,
        "created_at": now - timedelta(days=10),
        "updated_at": now - timedelta(days=8),
    },
    {
        "id": "req-sample-6",
        "requester_id": "user-alice",
        "plain_text": "20 replacement laptops needed for the Paris office due to hardware failure. No preferred supplier.",
        "status": "rejected",
        "title": "Replacement laptops – Paris",
        "business_unit": "Digital Workplace",
        "country": "FR",
        "site": "Paris",
        "category_l1": "IT",
        "category_l2": "Replacement / Break-Fix Pool Devices",
        "currency": "EUR",
        "budget_amount": 18000,
        "quantity": 20,
        "unit_of_measure": "units",
        "required_by_date": "2026-04-01",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["FR"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": now - timedelta(days=12),
        "updated_at": now - timedelta(days=9),
    },
    {
        "id": "req-sample-7",
        "requester_id": "user-alice",
        "plain_text": "SEM campaign management for Q2 product launch across DE and AT markets.",
        "status": "withdrawn",
        "title": "Q2 SEM campaign – DACH",
        "business_unit": "Digital Workplace",
        "country": "DE",
        "site": "Munich",
        "category_l1": "Marketing",
        "category_l2": "Search Engine Marketing (SEM)",
        "currency": "EUR",
        "budget_amount": 60000,
        "quantity": 1,
        "unit_of_measure": "campaign",
        "required_by_date": "2026-04-10",
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["DE", "AT"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": now - timedelta(days=20),
        "updated_at": now - timedelta(days=18),
    },
]

AUDIT_ENTRIES = [
    {"request_id": "req-sample-1", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-2", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-2", "actor_id": "user-bob",  "action": "reviewed", "notes": "Looks complete, forwarding for supplier evaluation."},
    {"request_id": "req-sample-3", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-3", "actor_id": "user-bob",  "action": "escalated", "notes": "Lead time infeasible — escalated to category head for guidance."},
    {"request_id": "req-sample-4", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-4", "actor_id": "user-bob",  "action": "approved", "notes": "Within budget and policy. Standard purchase approved."},
    {"request_id": "req-sample-5", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-5", "actor_id": "user-carol", "action": "reviewed", "notes": "Reviewed from category perspective — supplier selection looks appropriate."},
    {"request_id": "req-sample-6", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-6", "actor_id": "user-bob",  "action": "rejected", "notes": "Budget insufficient for the required spec. Please revise and resubmit."},
    {"request_id": "req-sample-7", "actor_id": "user-alice", "action": "submitted"},
    {"request_id": "req-sample-7", "actor_id": "user-alice", "action": "withdrawn", "notes": "Campaign postponed to Q3."},
]


def seed():
    db.query(models.AuditEntry).delete()
    db.query(models.Clarification).delete()
    db.query(models.Escalation).delete()
    db.query(models.Request).delete()
    db.query(models.User).delete()
    db.commit()

    for u in USERS:
        db.add(models.User(**u))
    db.commit()

    for r in SAMPLE_REQUESTS:
        db.add(models.Request(**r))
    db.commit()

    db.add(models.Escalation(
        id="esc-sample-1",
        request_id="req-sample-3",
        type="requester_clarification",
        target_user_id="user-alice",
        status="pending",
        message="Lead time is infeasible — all suppliers need 10+ days. Please confirm if the deadline can be extended or if partial delivery is acceptable.",
    ))
    db.commit()

    for entry in AUDIT_ENTRIES:
        db.add(models.AuditEntry(**entry))
    db.commit()

    print("✅ Database seeded successfully.")
    for u in USERS:
        print(f"  {u['id']:20s}  {u['name']:20s}  [{u['role']}]")


if __name__ == "__main__":
    seed()
    db.close()
