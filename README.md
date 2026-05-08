# Westbridge AI Assistant — Barbara Prototype

**Demo for May 12, 2026 in-person meeting at Westbridge Realty Group.**

A conversational AI agent that talks to (mocked) Westbridge systems. Steve named "Barbara" on the April 30 call — this is her v0.1.

---

## What it does

A chat interface where you can:

- **Ask questions** — *"What's our occupancy at 165 East?"*, *"Which tenants are most behind?"*
- **Surface data issues** — *"Any data quality problems I should know about?"*
- **Draft actions** — *"Pay invoice 4729 from the Williamsburg account"* → drafts a payment, requires explicit confirmation before executing

Behind the scenes: Llama 3.3 70B (Groq) decides which of 8 tools to call. Tools read from local mock JSON files shaped exactly like real Rent Manager + QuickBooks API responses. Day 1 of work, swap mock for live API — same code.

---

## Setup (one time)

```bash
cd demo
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# edit .env, paste your Groq API key (get one free at console.groq.com)
```

---

## Run it

```bash
streamlit run app.py
```

Browser opens to `http://localhost:8501`. Start typing.

---

## Demo paths to rehearse

Three rehearsed conversation flows, pick one based on the room:

### 1. The audit path (data quality)
> *"Any data issues across the portfolio I should know about?"*

Reveals Maria Cologne reconciliation, 12,000% occupancy bug, missing lease end-dates. AI explains each in plain English with severity + recommended action.

### 2. The Q&A path (asset manager mode)
> *"What's occupancy at Riverdale Heights?"*
> *"Which tenants are most behind on rent?"*
> *"Show me unpaid invoices over 30 days."*

Demonstrates the "AI Asset Manager" Steve described.

### 3. The action path (Barbara mode)
> *"Pay invoice 4729 from the Williamsburg account."*

Demonstrates draft → confirm → execute pattern. The literal "Barbara" Steve described. Shows that writes always require human confirmation.

---

## What's real vs. mocked

| Component | Status |
|---|---|
| The chat UI (Streamlit) | Real |
| Llama 3.3 70B inference (Groq) | Real |
| Tool calling architecture | Real (production pattern) |
| The 8 tools | Real Python functions |
| Backing data (tenants, leases, charges, invoices, bank accounts) | **Mocked** — JSON files matching Rent Manager + QBO API response shapes |

**Day 1 of employment**: replace JSON file reads with live API calls. Same code. ~30 minutes of work.

---

## Architecture

```
   User types in Streamlit chat
              │
              ▼
       Streamlit (app.py)
              │
              ▼
       Groq + Llama 3.3 70B  ◄──── system_prompt.md
              │
              │  decides which tool to call
              ▼
       tools.py  (8 functions)
              │
              ▼
       mock_data/*.json  ◄──── shaped like real RM + QBO responses
              │
              ▼
       result fed back to Llama
              │
              ▼
       natural-language reply in chat
```

---

## Files

```
demo/
├── README.md             # this file
├── requirements.txt      # streamlit, groq, dotenv
├── .env.example          # template for your API key
├── .env                  # your local key (gitignored)
├── system_prompt.md      # the agent's instructions
├── app.py                # Streamlit UI + agent loop
├── tools.py              # 8 tool definitions
└── mock_data/
    ├── properties.json
    ├── tenants.json
    ├── leases.json
    ├── charges.json
    ├── invoices.json
    ├── bank_accounts.json
    └── service_issues.json
```
