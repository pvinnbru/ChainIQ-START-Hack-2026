# ChainIQ @ START Hack 2026

## Audit-Ready Autonomous Sourcing Agent

## Overview

Large organizations often receive purchase requests that are incomplete, inconsistent, or urgent. Procurement professionals must interpret those requests, apply internal rules, compare suppliers, and later justify their decisions in audits. This process is manual, difficult to scale, and highly dependent on individual experience.

This challenge asks participants to build a **working prototype** that converts an **unstructured purchase request** into a **structured, defensible supplier comparison**. The system should apply procurement rules, detect contradictions or policy violations, explain its reasoning clearly, and demonstrate escalation logic when a compliant decision cannot be made automatically.

---

## Users

This solution is intended for:

- Procurement managers  
- Category buyers  
- Compliance and risk reviewers  
- Business stakeholders requesting purchases  

---

## Challenge Topic

**Sourcing Intelligence**

---

## Expected Outcome

Participants should build a prototype that can:

- Extract structured requirements from free-text purchase requests  
- Detect missing or contradictory information  
- Apply procurement rules and thresholds  
- Identify compliant supplier options  
- Present a ranked supplier comparison  
- Explain the reasoning behind its recommendation  
- Trigger escalation when a confident, compliant decision cannot be made automatically  

---

## Data

All core datasets will be provided in **machine-readable formats** such as **JSON** and **CSV**, allowing teams to focus on reasoning, decision logic, and system design.

### Provided Datasets

| File | Description |
|------|-------------|
| `requests.json` | Free-text purchase requests, including standard cases, missing information, conflicting requirements, requests exceeding thresholds, and requests referencing restricted suppliers |
| `suppliers.csv` | Supplier master data including category coverage, pricing tiers, lead times, geographic information, and basic risk flags |
| `pricing.csv` | Pricing structures, volume tiers, and minimum order quantities |
| `policies.json` | Procurement rules such as approval thresholds, preferred supplier lists, restricted suppliers, and category constraints |
| `historical_awards.csv` | Historical supplier decisions for reference |

### Optional Stretch Data

Additional data may be provided for more advanced solutions, such as:

- ESG scoring  
- Data residency rules  
- Additional regulatory constraints  

---

## Technology

- Azure credits are available  
- Teams may use **any language, UI framework, AI tooling, and/or rules engine** appropriate for their solution  

---

## Core Use Case

A stakeholder submits a purchase request, for example:

> “Need 500 laptops in 2 weeks, prefer Supplier X, budget 400k.”

The system must then:

1. Extract structured requirements  
2. Detect missing or contradictory information  
3. Apply procurement rules  
4. Identify compliant supplier options  
5. Present a ranked comparison  
6. Provide clear reasoning  
7. Flag when escalation is required  

---

## Business Value

This solution aims to improve:

- **Decision consistency**  
- **Compliance adherence**  
- **Audit transparency**  
- **Procurement cycle time**  

The goal is to support **scalable procurement operations** without removing **human oversight**.

---

## Optional Stretch Goals

Advanced teams may also choose to:

- Enforce geographic or regulatory constraints  
- Simulate approval routing logic  
- Implement confidence scoring  
- Generate a structured audit document  

These are **not required** for a valid submission, but can strengthen the overall solution.

---

## Judging Criteria

| Criteria | Weight | Description |
|----------|--------|-------------|
| **Creativity** | 20% | Clear, practical, and innovative approach to structuring requests and supplier comparison |
| **Visual Design** | 10% | Clarity of the comparison view and decision explanation |
| **Feasibility** | 25% | Realistic architecture and deployability |
| **Reachability** | 20% | Effectiveness in addressing real procurement challenges |
| **Robustness & Escalation Logic** | 25% | Ability to handle contradictions, rule violations, and uncertainty appropriately |

---

## Presentation Expectations

### Format
- **Live demo:** 5 minutes  
- **Explanation:** 3 minutes  

### Key Elements
Participants should include:

- A walkthrough of **one standard request**  
- A walkthrough of **one edge case**  
- A **supplier comparison view**  
- An explanation of **rule application**  
- A demonstration of **escalation handling**  

### Requirements
The presentation should show:

- A **working prototype**  
- **Clear reasoning logic**  
- A short explanation of the **system design**  
- A brief statement on how the solution could **scale in production**  

---

## Prize

The winning team will receive:

- **Paid internship (2–3 months)** with the opportunity to support implementation of the idea  
- **AirPods Max**  

---

## Goal

Build an intelligent sourcing assistant that helps procurement teams make faster, more consistent, and audit-ready supplier decisions — while ensuring compliance and preserving human oversight.
