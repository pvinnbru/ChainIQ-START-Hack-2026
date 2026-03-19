"""Run once to populate the database with demo users and sample requests.
Set SEED_DATA=false in .env to skip seeding (use file-based data instead)."""
import json
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
from database import engine, SessionLocal, Base
import models  # noqa: F401

SEED_DATA = os.environ.get("SEED_DATA", "true").lower() == "true"

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
    # ── Active requests with urgent deadlines ──────────────────────────────────
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
        "required_by_date": "2026-03-26",   # 7 days — amber
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
        "required_by_date": "2026-03-22",   # 3 days — red
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["CH", "DE"]),
        "data_residency_constraint": True,
        "esg_requirement": False,
        "created_at": now - timedelta(days=5),
        "updated_at": now - timedelta(days=1),
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
        "required_by_date": "2026-03-21",   # 2 days — red
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["DE"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": now - timedelta(days=3),
        "updated_at": now - timedelta(days=2),
    },
    # ── Escalated to procurement manager (Bob's queue) ─────────────────────────
    {
        "id": "req-sample-8",
        "requester_id": "user-alice",
        "plain_text": "240 docking stations for the Munich engineering team. Dell preferred, needed before end of sprint.",
        "status": "escalated",
        "title": "Docking stations – Munich engineering",
        "business_unit": "Digital Workplace",
        "country": "DE",
        "site": "Munich",
        "category_l1": "IT",
        "category_l2": "Docking Stations",
        "currency": "EUR",
        "budget_amount": 25200,
        "quantity": 240,
        "unit_of_measure": "units",
        "required_by_date": "2026-03-20",   # 1 day — critical
        "preferred_supplier_mentioned": "Dell Enterprise Europe",
        "incumbent_supplier": "Bechtle Workplace Solutions",
        "contract_type_requested": "purchase",
        "delivery_countries": json.dumps(["DE"]),
        "data_residency_constraint": False,
        "esg_requirement": False,
        "created_at": now - timedelta(days=5),
        "updated_at": now - timedelta(days=1),
    },
    # ── Escalated to category head (Carol's queue) ─────────────────────────────
    {
        "id": "req-sample-9",
        "requester_id": "user-alice",
        "plain_text": "We need to procure a SIEM platform license for 3 years. Splunk preferred but open to alternatives if TCO is significantly lower.",
        "status": "escalated",
        "title": "SIEM platform license – 3-year term",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Software",
        "category_l2": "Security Software",
        "currency": "CHF",
        "budget_amount": 420000,
        "quantity": 1,
        "unit_of_measure": "license",
        "required_by_date": "2026-04-01",
        "preferred_supplier_mentioned": "Splunk",
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["CH"]),
        "data_residency_constraint": True,
        "esg_requirement": False,
        "created_at": now - timedelta(days=4),
        "updated_at": now - timedelta(days=1),
    },
    # ── Escalated to compliance (Dave's queue) ─────────────────────────────────
    {
        "id": "req-sample-10",
        "requester_id": "user-alice",
        "plain_text": "Engage external legal counsel for GDPR compliance review of our new data processing agreements with three vendors.",
        "status": "escalated",
        "title": "External legal counsel – GDPR DPA review",
        "business_unit": "Digital Workplace",
        "country": "CH",
        "site": "Zürich",
        "category_l1": "Professional Services",
        "category_l2": "Legal Services",
        "currency": "CHF",
        "budget_amount": 35000,
        "quantity": 15,
        "unit_of_measure": "consulting_day",
        "required_by_date": "2026-03-17",   # overdue
        "contract_type_requested": "service",
        "delivery_countries": json.dumps(["CH", "DE", "FR"]),
        "data_residency_constraint": True,
        "esg_requirement": False,
        "created_at": now - timedelta(days=8),
        "updated_at": now - timedelta(days=2),
    },
    # ── Completed requests ─────────────────────────────────────────────────────
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

ESCALATIONS = [
    # Alice: clarification needed on monitors (req-3)
    {
        "id": "esc-sample-1",
        "request_id": "req-sample-3",
        "type": "requester_clarification",
        "target_user_id": "user-alice",
        "status": "pending",
        "message": "Lead time is infeasible — all suppliers need 10+ days. Please confirm if the 21 Mar deadline can be extended or if partial delivery is acceptable.",
    },
    # Bob: procurement manager review on docking stations (req-8)
    {
        "id": "esc-sample-2",
        "request_id": "req-sample-8",
        "type": "procurement_manager",
        "target_user_id": "user-bob",
        "status": "pending",
        "message": "Budget of EUR 25,200 is insufficient — lowest compliant price for 240 units is EUR 35,712. Requester instruction 'single supplier only' also conflicts with AT-002 (2 quotes required above EUR 25k). Procurement Manager approval needed.",
    },
    # Carol: category head review on SIEM license (req-9)
    {
        "id": "esc-sample-3",
        "request_id": "req-sample-9",
        "type": "category_head",
        "target_user_id": "user-carol",
        "status": "pending",
        "message": "Contract value CHF 420,000 triggers AT-003 (above CHF 250k). Category Head sign-off required before sourcing can proceed. Please confirm preferred supplier strategy for Security Software.",
    },
    # Dave: compliance review on legal services (req-10)
    {
        "id": "esc-sample-4",
        "request_id": "req-sample-10",
        "type": "compliance",
        "target_user_id": "user-dave",
        "status": "pending",
        "message": "Engagement of external legal counsel for GDPR DPA review requires Compliance sign-off per policy CR-007. Data residency constraint flagged — delivery spans CH, DE, FR. Please verify cross-border data handling requirements.",
    },
]

AUDIT_ENTRIES = [
    # req-1: submitted, awaiting review
    {"request_id": "req-sample-1", "actor_id": "user-alice", "action": "submitted", "created_at": now - timedelta(days=7)},

    # req-2: submitted → reviewed → back to pending
    {"request_id": "req-sample-2", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=5)},
    {"request_id": "req-sample-2", "actor_id": "user-bob",   "action": "reviewed",   "notes": "Initial review complete — data residency constraint noted. Forwarding for supplier evaluation.", "created_at": now - timedelta(days=1)},

    # req-3: submitted → escalated (requester clarification)
    {"request_id": "req-sample-3", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=3)},
    {"request_id": "req-sample-3", "actor_id": "user-bob",   "action": "escalated",  "notes": "Lead time infeasible — all suppliers require 10+ days minimum. Deadline of 21 Mar cannot be met. Requesting clarification from Alice.", "created_at": now - timedelta(days=2)},

    # req-4: full approval flow
    {"request_id": "req-sample-4", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=14)},
    {"request_id": "req-sample-4", "actor_id": "user-bob",   "action": "reviewed",   "notes": "ESG requirement verified — Vitra and Steelcase both qualify. Budget is within AT-001 threshold.", "created_at": now - timedelta(days=12)},
    {"request_id": "req-sample-4", "actor_id": "user-bob",   "action": "approved",   "notes": "Within budget and policy. Standard purchase approved. Proceed with Steelcase as incumbent.", "created_at": now - timedelta(days=10)},

    # req-5: submitted → reviewed by carol
    {"request_id": "req-sample-5", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=10)},
    {"request_id": "req-sample-5", "actor_id": "user-bob",   "action": "reviewed",   "notes": "Checked budget and timeline — looks reasonable for scope.", "created_at": now - timedelta(days=9)},
    {"request_id": "req-sample-5", "actor_id": "user-carol", "action": "reviewed",   "notes": "Reviewed from category perspective — CrowdStrike and Deloitte Cyber both on preferred list for CH. Supplier selection looks appropriate.", "created_at": now - timedelta(days=8)},

    # req-6: submitted → rejected
    {"request_id": "req-sample-6", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=12)},
    {"request_id": "req-sample-6", "actor_id": "user-bob",   "action": "reviewed",   "notes": "Budget appears low for spec. Checking pricing.", "created_at": now - timedelta(days=11)},
    {"request_id": "req-sample-6", "actor_id": "user-bob",   "action": "rejected",   "notes": "EUR 18,000 is insufficient — minimum for 20 units meeting spec is EUR 24,400. Please revise budget and resubmit.", "created_at": now - timedelta(days=9)},

    # req-7: submitted → withdrawn
    {"request_id": "req-sample-7", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=20)},
    {"request_id": "req-sample-7", "actor_id": "user-alice", "action": "withdrawn",  "notes": "Campaign postponed to Q3 — budget reallocated to product development.", "created_at": now - timedelta(days=18)},

    # req-8: submitted → escalated (procurement manager)
    {"request_id": "req-sample-8", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=5)},
    {"request_id": "req-sample-8", "actor_id": "user-bob",   "action": "escalated",  "notes": "Budget insufficient and single-supplier instruction conflicts with AT-002. Escalating to Procurement Manager for deviation approval.", "created_at": now - timedelta(days=1)},

    # req-9: submitted → escalated (category head)
    {"request_id": "req-sample-9", "actor_id": "user-alice", "action": "submitted",  "created_at": now - timedelta(days=4)},
    {"request_id": "req-sample-9", "actor_id": "user-bob",   "action": "reviewed",   "notes": "Contract value exceeds AT-003 threshold of CHF 250k. Category Head sign-off required.", "created_at": now - timedelta(days=2)},
    {"request_id": "req-sample-9", "actor_id": "user-bob",   "action": "escalated",  "notes": "Escalating to Carol (Head of IT Category) per AT-003.", "created_at": now - timedelta(days=1)},

    # req-10: submitted → escalated (compliance)
    {"request_id": "req-sample-10", "actor_id": "user-alice", "action": "submitted", "created_at": now - timedelta(days=8)},
    {"request_id": "req-sample-10", "actor_id": "user-bob",   "action": "reviewed",  "notes": "Legal services engagement looks scoped correctly. Data residency across 3 countries needs Compliance sign-off per CR-007.", "created_at": now - timedelta(days=5)},
    {"request_id": "req-sample-10", "actor_id": "user-bob",   "action": "escalated", "notes": "Escalating to Dave (Compliance) — cross-border GDPR implications require CR-007 clearance.", "created_at": now - timedelta(days=2)},
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

    for e in ESCALATIONS:
        db.add(models.Escalation(**e))
    db.commit()

    for entry in AUDIT_ENTRIES:
        notes = entry.get("notes")
        created_at = entry.get("created_at", now)
        db.add(models.AuditEntry(
            request_id=entry["request_id"],
            actor_id=entry["actor_id"],
            action=entry["action"],
            notes=notes,
            created_at=created_at,
        ))
    db.commit()

    print("✅ Database seeded successfully.")
    for u in USERS:
        print(f"  {u['id']:20s}  {u['name']:20s}  [{u['role']}]")
    print(f"\n  {len(SAMPLE_REQUESTS)} requests, {len(ESCALATIONS)} escalations, {len(AUDIT_ENTRIES)} audit entries")


if __name__ == "__main__":
    if not SEED_DATA:
        print("SEED_DATA=false — skipping seed (using file-based data from requests.json)")
        sys.exit(0)
    seed()
    db.close()
