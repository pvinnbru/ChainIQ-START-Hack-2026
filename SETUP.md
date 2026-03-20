# ChainIQ — Setup & Startup Guide

## Prerequisites

- **Python 3.13+** with `pip`
- **Node.js 20+** with `npm`
- Azure OpenAI API credentials (already in `.env`)
- Slack Bot & App tokens (already in `.env`)

---

## 1. Environment Variables

All secrets live in the root **`.env`** file. The backend loads it automatically via `python-dotenv`. Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite connection string (default: `sqlite:///./chainiq.db`) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name (e.g. `gpt-4o`) |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Slack app-level token (`xapp-...`) |
| `CHAINIQ_APP_URL` | Frontend URL for links in notifications (default: `http://localhost:3000`) |
| `SEED_DATA` | Set to `true` to seed demo data on startup, `false` to skip |

---

## 2. Preprocessing: Rules & Historical Data

Before the platform can evaluate procurement requests, the rule engine and historical pricing data must be preprocessed. These steps use the **Azure OpenAI API** to convert policy documents into executable action tuples.

### 2.1 Ingest Policy Rules (LLM-powered)

Policy rules live in `data/policies.json`. The ingestion pipeline reads each rule, sends it to the LLM with the schema (`request-evaluation/start_dict.csv`), and produces sorted action tuples stored in `stores/`.

```bash
cd request-evaluation

# Install dependencies (if not already)
pip install openai python-dotenv

# Build all action stores (approval thresholds, category rules, escalation rules)
# This calls the LLM for each rule — cached via SHA-256 hash of data/ + schema
python -c "from actions_store import build_all_stores_parallel; build_all_stores_parallel()"
```

**Output files** (in `stores/`):
- `approval_thresholds_actions.json` — budget-based quoting & escalation rules
- `category_rules_actions.json` — category-specific compliance gates
- `escalation_rules_actions.json` — natural-language escalation triggers

> **Caching:** The stores are keyed by a SHA-256 hash of `data/` + `start_dict.csv`. If neither changes, subsequent calls return instantly from cache. To force a rebuild, delete the JSON files in `stores/`.

### 2.2 Ingest Historical Awards

Historical procurement data (`data/historical_awards.csv`) is used to compute per-category pricing stats and per-supplier reputation scores.

```bash
cd request-evaluation
python ingest_historical_awards.py
```

**Output:** `stores/historical_data.json` — contains average unit prices, standard deviations, and supplier historic scores.

---

## 3. Backend (FastAPI)

```bash
cd backend

# Create a virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Start the API server (auto-reloads on changes)
uvicorn main:app --reload
```

The backend starts on **http://localhost:8000**. On first launch it:
1. Creates the SQLite database (`chainiq.db`)
2. Seeds demo users & sample requests (if `SEED_DATA=true`)
3. Pre-warms the evaluation pipeline (loads action stores into memory)

---

## 4. Slack Bot

The Slack bot runs as a separate process using Socket Mode:

```bash
cd backend
source venv/bin/activate

python bot_slack.py
```

> Requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env`.

Users can DM the bot or mention it in a channel with a procurement request in natural language. The bot will:
1. Create the request in the database
2. Run the full AI evaluation pipeline
3. Reply with a confirmation (no supplier details shown to requesters)

---

## 5. Frontend (Next.js)

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server
npm run dev
```

The frontend starts on **http://localhost:3000**.

---

## 6. Quick Start (All Three Together)

Open three terminal tabs and run:

```bash
# Terminal 1 — Backend API
cd backend && source venv/bin/activate && uvicorn main:app --reload

# Terminal 2 — Slack Bot
cd backend && source venv/bin/activate && python bot_slack.py

# Terminal 3 — Frontend
cd frontend && npm run dev
```

Then open **http://localhost:3000** in your browser. Choose a demo persona to log in.

---

## Project Structure

```
ChainIQ-START-Hack-2026/
├── data/                          # Raw input data
│   ├── policies.json              #   Procurement policy rules
│   ├── suppliers.csv              #   Supplier catalog
│   ├── categories.csv             #   Category taxonomy
│   ├── historical_awards.csv      #   Past procurement awards
│   ├── pricing.csv                #   Supplier pricing tiers
│   └── requests.json              #   Sample requests (for seeding)
├── stores/                        # Preprocessed caches (auto-generated)
│   ├── approval_thresholds_actions.json
│   ├── category_rules_actions.json
│   ├── escalation_rules_actions.json
│   └── historical_data.json
├── request-evaluation/            # AI evaluation engine
│   ├── evaluate_request.py        #   Main evaluation pipeline
│   ├── supplier_matrix.py         #   Supplier ranking algorithm
│   ├── escalation_engine.py       #   Escalation trigger logic
│   ├── result_flags.py            #   Post-evaluation flag checks
│   ├── rule_ingestion_prompt.py   #   LLM prompts for rule ingestion
│   ├── actions_store.py           #   Rule cache with hash invalidation
│   ├── ingest_historical_awards.py#   Historical data preprocessor
│   ├── sort_actions.py            #   Topological sort for actions
│   └── start_dict.csv             #   Schema dictionary
├── backend/                       # FastAPI application
│   ├── main.py                    #   App entry point
│   ├── models.py                  #   SQLAlchemy models
│   ├── schemas.py                 #   Pydantic schemas
│   ├── seed.py                    #   Demo data seeder
│   ├── bot_slack.py               #   Slack bot (Socket Mode)
│   ├── notifications.py           #   Slack DM notifications
│   ├── routers/                   #   API route handlers
│   └── services/                  #   Business logic layer
├── frontend/                      # Next.js application
│   ├── app/                       #   Pages (App Router)
│   ├── components/                #   UI components
│   └── context/                   #   React contexts (auth, etc.)
└── .env                           # Environment variables
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED` on Slack | Run `open /Applications/Python\ 3.13/Install\ Certificates.command` or set `SSL_CERT_FILE` |
| Backend can't find `evaluate_request` | Ensure the root project dir is in `PYTHONPATH` or run from `backend/` |
| Rules not updating after editing `policies.json` | Delete `stores/*_actions.json` to force LLM re-ingestion |
| Empty supplier ranking | Check that `stores/historical_data.json` exists; run `python ingest_historical_awards.py` |
