"""Tool functions for the Westbridge AI Assistant (Barbara prototype).

Each function reads from mock_data/*.json files shaped exactly like real
Rent Manager + QuickBooks API responses. Day 1 of employment, swap these
for live API calls — same function signatures, same return shapes.
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "mock_data"

# In-memory store of payment drafts (cleared on app restart).
PAYMENT_DRAFTS: dict[str, dict] = {}


def _load(name: str) -> list[dict]:
    with open(DATA_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def _ci_contains(haystack: str | None, needle: str) -> bool:
    return haystack is not None and needle.lower() in haystack.lower()


_NOISE_TOKENS = {"llc", "inc", "corp", "co", "the", "and", "of", "ltd"}


def _tokens(s: str | None) -> set[str]:
    """Lowercase tokens of length > 1, filtering common entity-suffix noise."""
    if not s:
        return set()
    cleaned = s.replace("—", " ").replace("-", " ").replace(".", " ")
    return {
        w.lower() for w in cleaned.split()
        if len(w) > 1 and w.lower() not in _NOISE_TOKENS
    }


def _token_overlap(field: str | None, query: str | None) -> bool:
    """True if any meaningful token in `query` also appears in `field`."""
    if not field or not query:
        return False
    return bool(_tokens(field) & _tokens(query))


def _fuzzy_match(field: str | None, query: str) -> bool:
    """Substring-or-token-overlap match. Use for fuzzy name lookups."""
    return _ci_contains(field, query) or _token_overlap(field, query)


# ─── Tool 1 ──────────────────────────────────────────────────────────────
def get_property_info(property_query: str) -> dict:
    """Look up a property by name (full or partial), address, or ID."""
    properties = _load("properties")
    for p in properties:
        if (
            str(p["Id"]) == property_query
            or _fuzzy_match(p["Name"], property_query)
            or _fuzzy_match(p["Address"], property_query)
            or _fuzzy_match(p["OwnerLLC"], property_query)
        ):
            return p
    return {"error": f"No property found matching '{property_query}'"}


# ─── Tool 2 ──────────────────────────────────────────────────────────────
def list_tenants(property_query: str | None = None, status: str | None = None) -> list[dict]:
    """List tenants. Optionally filter by property (name/ID) and/or status."""
    tenants = _load("tenants")
    results = tenants

    if property_query:
        results = [
            t for t in results
            if str(t["PropertyId"]) == property_query
            or _fuzzy_match(t["PropertyName"], property_query)
        ]

    if status:
        results = [t for t in results if _ci_contains(t["Status"], status)]

    return results


# ─── Tool 3 ──────────────────────────────────────────────────────────────
def get_tenant_balance(tenant_query: str) -> dict:
    """Get a tenant's current balance, status, and recent charges/payments."""
    tenants = _load("tenants")
    matched = None
    for t in tenants:
        if str(t["Id"]) == tenant_query:
            matched = t
            break
        full_name = f"{t['FirstName']} {t['LastName']}"
        if _ci_contains(full_name, tenant_query) or _ci_contains(t["LastName"], tenant_query):
            matched = t
            break

    if not matched:
        return {"error": f"No tenant found matching '{tenant_query}'"}

    charges = [c for c in _load("charges") if c["TenantId"] == matched["Id"]]
    return {
        "tenant": matched,
        "recent_charges": sorted(charges, key=lambda c: c["Date"], reverse=True)[:8],
    }


# ─── Tool 4 ──────────────────────────────────────────────────────────────
def find_invoice(invoice_id: int) -> dict:
    """Look up a vendor invoice by its ID."""
    for inv in _load("invoices"):
        if inv["Id"] == invoice_id:
            return inv
    return {"error": f"No invoice found with ID {invoice_id}"}


# ─── Tool 5 ──────────────────────────────────────────────────────────────
def list_unpaid_invoices(
    min_age_days: int = 0,
    property_query: str | None = None,
) -> list[dict]:
    """List unpaid invoices. Filter by minimum days past due and/or property."""
    invoices = _load("invoices")
    results = [
        i for i in invoices
        if i["Status"] == "Unpaid" and i.get("DaysPastDue", 0) >= min_age_days
    ]
    if property_query:
        results = [
            i for i in results
            if _ci_contains(i.get("PropertyName"), property_query)
            or _ci_contains(i.get("LLC"), property_query)
        ]
    return sorted(results, key=lambda i: i.get("DaysPastDue", 0), reverse=True)


# ─── Tool 6 ──────────────────────────────────────────────────────────────
def get_bank_accounts(llc_or_property_query: str | None = None) -> list[dict]:
    """List bank accounts. Filter by LLC name or property name (fuzzy)."""
    accounts = _load("bank_accounts")
    if not llc_or_property_query:
        return accounts

    q = llc_or_property_query
    properties = _load("properties")
    matched_property_ids = {
        p["Id"] for p in properties
        if _fuzzy_match(p["Name"], q)
        or _fuzzy_match(p["Address"], q)
        or _fuzzy_match(p["OwnerLLC"], q)
    }

    return [
        a for a in accounts
        if _fuzzy_match(a["LLC"], q)
        or _fuzzy_match(a["AccountName"], q)
        or a.get("PropertyId") in matched_property_ids
    ]


# ─── Tool 7 ──────────────────────────────────────────────────────────────
def draft_payment(invoice_id: int, bank_account_id: int) -> dict:
    """Draft a payment for a vendor invoice. Does NOT execute.

    Returns a draft record with a draft_id. To execute, the user must
    explicitly confirm and the agent must call execute_payment(draft_id).
    """
    invoice = find_invoice(invoice_id)
    if "error" in invoice:
        return invoice
    if invoice["Status"] != "Unpaid":
        return {
            "error": f"Invoice {invoice_id} status is '{invoice['Status']}', not Unpaid. Refusing to draft."
        }

    accounts = _load("bank_accounts")
    bank = next((a for a in accounts if a["Id"] == bank_account_id), None)
    if not bank:
        return {"error": f"No bank account found with ID {bank_account_id}"}

    # Sanity check: warn if the LLCs don't match
    warning = None
    if bank["LLC"] != invoice["LLC"]:
        warning = (
            f"LLC mismatch: invoice belongs to {invoice['LLC']} but bank "
            f"account is on {bank['LLC']}. Proceeding requires confirmation."
        )

    draft_id = "DRAFT-" + secrets.token_hex(4).upper()
    draft = {
        "draft_id": draft_id,
        "invoice_id": invoice_id,
        "vendor_name": invoice["VendorName"],
        "amount": invoice["Amount"],
        "from_account": bank["AccountName"],
        "from_account_masked": bank["AccountNumberMasked"],
        "to_property": invoice.get("PropertyName"),
        "description": invoice.get("Description"),
        "warning": warning,
        "status": "Drafted (awaiting confirmation)",
        "drafted_at": datetime.now().isoformat(timespec="seconds"),
    }
    PAYMENT_DRAFTS[draft_id] = draft
    return draft


# ─── Tool 8 ──────────────────────────────────────────────────────────────
def execute_payment(draft_id: str) -> dict:
    """Execute a previously-drafted payment. ONLY call after explicit user confirmation."""
    draft = PAYMENT_DRAFTS.get(draft_id)
    if not draft:
        return {"error": f"No payment draft found with ID {draft_id}"}
    if draft.get("executed_at"):
        return {"error": f"Payment {draft_id} has already been executed."}

    confirmation_number = "WB-" + secrets.token_hex(5).upper()
    draft["executed_at"] = datetime.now().isoformat(timespec="seconds")
    draft["confirmation_number"] = confirmation_number
    draft["status"] = "Executed (mock — no real money moved)"
    return draft


# ─── Tool 9 ──────────────────────────────────────────────────────────────
def audit_data_quality(property_query: str | None = None) -> dict:
    """Run data quality validation across Rent Manager data.

    Checks for: occupancy > 100% bugs, balance/status mismatches, missing
    lease end dates, vendor name duplicates, and other data hygiene issues.
    Optionally scope to one property.
    """
    properties = _load("properties")
    tenants = _load("tenants")
    leases = _load("leases")
    invoices = _load("invoices")

    if property_query:
        properties = [
            p for p in properties
            if _ci_contains(p["Name"], property_query) or str(p["Id"]) == property_query
        ]
        property_ids = {p["Id"] for p in properties}
        tenants = [t for t in tenants if t["PropertyId"] in property_ids]
        leases = [l for l in leases if l["PropertyId"] in property_ids]

    findings: list[dict] = []

    # Rule 1: occupancy > 100%
    for p in properties:
        if p.get("OccupancyPct") is not None and p["OccupancyPct"] > 100:
            findings.append({
                "rule": "occupancy_over_100",
                "severity": "Critical",
                "property": p["Name"],
                "detail": f"Occupancy reported as {p['OccupancyPct']}% — clear data integrity issue.",
                "recommended_action": "Recompute from OccupiedUnits/UnitCount; root-cause the source of the bad value in Rent Manager.",
            })

    # Rule 2: tenant in litigation with high balance
    for t in tenants:
        if "litigation" in (t.get("Status") or "").lower() and (t.get("Balance") or 0) > 10000:
            findings.append({
                "rule": "litigation_balance_mismatch",
                "severity": "High",
                "property": t["PropertyName"],
                "tenant": f"{t['FirstName']} {t['LastName']} (Unit {t['Unit']})",
                "detail": f"Balance of ${t['Balance']:,.2f} on litigation tenant — likely needs reconciliation with legal team.",
                "recommended_action": "Reconcile Rent Manager balance with legal team's records before any reporting.",
            })

    # Rule 3: missing lease end dates
    missing_end_dates = [l for l in leases if l.get("EndDate") is None]
    if missing_end_dates:
        findings.append({
            "rule": "missing_lease_end_dates",
            "severity": "Medium",
            "detail": f"{len(missing_end_dates)} lease(s) missing EndDate.",
            "examples": [
                f"{l['TenantName']} (Unit {l['Unit']})" for l in missing_end_dates[:5]
            ],
            "recommended_action": "Audit and backfill EndDate from signed lease PDFs. Add validation rule on lease creation.",
        })

    # Rule 4: balance vs charges sanity check
    for t in tenants:
        if t.get("Balance", 0) <= 0:
            continue
        # Heuristic: balance shouldn't exceed 10x monthly rent unless flagged
        lease = next((l for l in leases if l.get("TenantId") == t["Id"]), None)
        if lease and t["Balance"] > lease["MonthlyRent"] * 10:
            if not any(f.get("tenant", "").startswith(t["FirstName"]) for f in findings):
                findings.append({
                    "rule": "balance_far_exceeds_rent",
                    "severity": "High",
                    "property": t["PropertyName"],
                    "tenant": f"{t['FirstName']} {t['LastName']} (Unit {t['Unit']})",
                    "detail": f"Balance ${t['Balance']:,.2f} is {t['Balance'] / lease['MonthlyRent']:.0f}x monthly rent of ${lease['MonthlyRent']:,.2f}.",
                    "recommended_action": "Verify charge ledger; check for duplicated or stale charges.",
                })

    # Rule 5: vendor name duplicates (fuzzy)
    vendor_names = list({i["VendorName"] for i in invoices})
    seen_normalized: dict[str, list[str]] = {}
    for v in vendor_names:
        normalized = "".join(c for c in v.lower() if c.isalnum())[:8]
        seen_normalized.setdefault(normalized, []).append(v)
    duplicates = [v for v in seen_normalized.values() if len(v) > 1]
    if duplicates:
        findings.append({
            "rule": "vendor_name_duplicates",
            "severity": "Medium",
            "detail": f"{len(duplicates)} apparent vendor duplicate(s) detected.",
            "examples": duplicates,
            "recommended_action": "Consolidate vendor records to prevent payment confusion and improve spend reporting.",
        })

    return {
        "scope": property_query or "Entire portfolio",
        "audit_run_at": datetime.now().isoformat(timespec="seconds"),
        "findings_count": len(findings),
        "findings": findings,
    }


# ─── Tool registry: function names → callables ──────────────────────────
TOOL_FUNCTIONS = {
    "get_property_info": get_property_info,
    "list_tenants": list_tenants,
    "get_tenant_balance": get_tenant_balance,
    "find_invoice": find_invoice,
    "list_unpaid_invoices": list_unpaid_invoices,
    "get_bank_accounts": get_bank_accounts,
    "draft_payment": draft_payment,
    "execute_payment": execute_payment,
    "audit_data_quality": audit_data_quality,
}


# ─── JSON Schemas for the agent (OpenAI / Groq function-calling format) ─
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_property_info",
            "description": "Look up a Westbridge property by name (full or partial), address, or ID. Returns property details including unit count, occupancy, owner LLC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_query": {
                        "type": "string",
                        "description": "Property name, partial name, address, LLC name, or numeric ID.",
                    }
                },
                "required": ["property_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tenants",
            "description": "List tenants in the portfolio. Optionally filter by property and/or status (e.g., 'Active', 'In Litigation', 'Notice Given').",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_query": {
                        "type": "string",
                        "description": "Optional property name or ID to filter by.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional status filter (partial match: e.g., 'litigation', 'active').",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tenant_balance",
            "description": "Get a specific tenant's current balance, status, and recent charge history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant_query": {
                        "type": "string",
                        "description": "Tenant's full name, last name, or numeric ID.",
                    }
                },
                "required": ["tenant_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_invoice",
            "description": "Look up a single vendor invoice by its ID. Returns vendor, amount, status, dates, and the property/LLC it belongs to.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "The numeric invoice ID.",
                    }
                },
                "required": ["invoice_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_unpaid_invoices",
            "description": "List all unpaid vendor invoices. Optionally filter by minimum days past due and/or property.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_age_days": {
                        "type": "integer",
                        "description": "Minimum days past due (e.g., 30 to see only invoices >30 days late). Default 0.",
                        "default": 0,
                    },
                    "property_query": {
                        "type": "string",
                        "description": "Optional property name or LLC to filter by.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bank_accounts",
            "description": "List Westbridge bank accounts. Optionally filter by LLC name or property name (fuzzy match).",
            "parameters": {
                "type": "object",
                "properties": {
                    "llc_or_property_query": {
                        "type": "string",
                        "description": "Optional LLC name or property name to filter by (e.g., 'Williamsburg', 'Riverdale').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_payment",
            "description": "Draft a payment for a vendor invoice. ALWAYS call this first when the user asks to pay something. The payment is NOT executed — it returns a draft_id which must be passed to execute_payment ONLY after the user explicitly confirms.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "The invoice ID to pay.",
                    },
                    "bank_account_id": {
                        "type": "integer",
                        "description": "The bank account ID to pay from.",
                    },
                },
                "required": ["invoice_id", "bank_account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_payment",
            "description": "Execute a previously-drafted payment. ONLY call this after the user has explicitly confirmed (said 'yes', 'confirm', 'proceed', etc.) the draft. Never call this without confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft_id returned by draft_payment.",
                    }
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_data_quality",
            "description": "Run data quality checks across Rent Manager data. Detects occupancy bugs, balance/status mismatches, missing lease end dates, vendor duplicates, and other data hygiene issues. Optionally scope to one property.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_query": {
                        "type": "string",
                        "description": "Optional property name or ID to scope the audit to.",
                    }
                },
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch a tool call by name with parsed JSON arguments."""
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**arguments)
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"Tool {name} raised: {type(e).__name__}: {e}"}
