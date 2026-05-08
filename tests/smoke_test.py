"""Smoke test for the Westbridge AI Assistant agent.

Runs a battery of representative queries against the live Groq + Llama
agent and prints a summary. Useful for verifying behavior before a demo
or after any prompt/tool change.

Run from the demo folder:
    python tests/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Allow `from tools import ...` when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # type: ignore
from groq import Groq  # type: ignore

from tools import TOOL_SCHEMAS, execute_tool  # noqa: E402

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
SYSTEM_PROMPT = (ROOT / "system_prompt.md").read_text(encoding="utf-8")
MAX_ITERATIONS = 8

# (label, prompt, expected_kind, must_NOT_call_tools, must_call_tools)
TEST_CASES: list[tuple[str, str, str, list[str], list[str]]] = [
    # ── Information requests — must NOT draft payments ─────────────────
    (
        "info: unpaid >30",
        "Show me unpaid invoices over 30 days.",
        "info",
        ["draft_payment", "execute_payment"],
        ["list_unpaid_invoices"],
    ),
    (
        "info: occupancy",
        "What's our occupancy at Riverdale Heights?",
        "info",
        ["draft_payment", "execute_payment"],
        ["get_property_info"],
    ),
    (
        "info: litigation",
        "How many tenants do we have in litigation?",
        "info",
        ["draft_payment", "execute_payment"],
        ["list_tenants"],
    ),
    (
        "info: bank accounts",
        "List all bank accounts.",
        "info",
        ["draft_payment", "execute_payment"],
        ["get_bank_accounts"],
    ),
    (
        "info: data quality",
        "Any data quality issues across the portfolio?",
        "info",
        ["draft_payment", "execute_payment"],
        ["audit_data_quality"],
    ),
    (
        "info: largest invoice",
        "What's the largest unpaid invoice?",
        "info",
        ["draft_payment", "execute_payment"],
        ["list_unpaid_invoices"],
    ),
    (
        "info: tenant balance",
        "What's the balance on Maria Cologne?",
        "info",
        ["draft_payment", "execute_payment"],
        ["get_tenant_balance"],
    ),
    (
        "info: 165 East LLC accounts",
        "List bank accounts for the 165 East LLC.",
        "info",
        ["draft_payment", "execute_payment"],
        ["get_bank_accounts"],
    ),

    # ── Action requests — SHOULD draft, must wait for confirmation ─────
    (
        "action: pay invoice",
        "Pay invoice 4729 from the Williamsburg account.",
        "action",
        ["execute_payment"],  # must NOT execute without user confirm
        ["draft_payment"],
    ),

    # ── Edge cases ─────────────────────────────────────────────────────
    (
        "edge: out of scope",
        "What's the weather today?",
        "info",
        ["draft_payment", "execute_payment"],
        [],  # no tools required
    ),
    (
        "edge: nonsense",
        "How many elephants live at Riverdale Heights?",
        "info",
        ["draft_payment", "execute_payment"],
        [],
    ),
]


class AgentRun:
    def __init__(self, query: str) -> None:
        self.query = query
        self.tool_calls: list[tuple[str, dict]] = []
        self.duplicate_count = 0
        self.final_answer: str = ""
        self.iterations = 0
        self.error: str | None = None
        self.elapsed_sec: float = 0.0


def run_agent(query: str) -> AgentRun:
    run = AgentRun(query)
    start = time.time()
    client = Groq(api_key=GROQ_API_KEY)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        for _ in range(MAX_ITERATIONS):
            run.iterations += 1
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = response.choices[0].message
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(entry)

            if not msg.tool_calls:
                run.final_answer = msg.content or ""
                break

            # Group dupes (mirror the app behavior)
            groups: dict[tuple[str, str], list] = {}
            for tc in msg.tool_calls:
                key = (tc.function.name, tc.function.arguments or "{}")
                groups.setdefault(key, []).append(tc)

            for (name, args_json), tcs in groups.items():
                try:
                    args = json.loads(args_json)
                except json.JSONDecodeError:
                    args = {}
                run.tool_calls.append((name, args))
                if len(tcs) > 1:
                    run.duplicate_count += len(tcs) - 1
                result_json = json.dumps(execute_tool(name, args), default=str)
                for tc in tcs:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": result_json,
                    })
        else:
            run.error = "Max iterations reached without final answer"
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}"

    run.elapsed_sec = time.time() - start
    return run


def evaluate(case, run: AgentRun) -> tuple[bool, list[str]]:
    label, prompt, _, must_not_call, must_call = case
    issues: list[str] = []
    called_names = {n for n, _ in run.tool_calls}

    if run.error:
        issues.append(f"ERROR: {run.error}")
        return False, issues

    for forbidden in must_not_call:
        if forbidden in called_names:
            issues.append(f"called forbidden tool: {forbidden}")

    for required in must_call:
        if required not in called_names:
            issues.append(f"missed required tool: {required}")

    if run.duplicate_count > 0:
        issues.append(f"emitted {run.duplicate_count} duplicate tool call(s) (collapsed)")

    if not run.final_answer:
        issues.append("no final answer text")

    return not any(i.startswith(("ERROR", "called forbidden", "missed required", "no final")) for i in issues), issues


def main() -> int:
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set in .env")
        return 1

    print(f"Running {len(TEST_CASES)} test cases against {GROQ_MODEL}...\n")
    passed = 0
    failed = 0
    rows: list[tuple[str, str, AgentRun, bool, list[str]]] = []

    for case in TEST_CASES:
        label, prompt, kind, _, _ = case
        print(f"  > {label:30s} [{kind}] ...", end=" ", flush=True)
        run = run_agent(prompt)
        ok, issues = evaluate(case, run)
        rows.append((label, prompt, run, ok, issues))
        status = "PASS" if ok else "FAIL"
        print(f"{status} ({run.elapsed_sec:.1f}s, {len(run.tool_calls)} tool call(s))")
        if not ok:
            for i in issues:
                print(f"      - {i}")
        passed += int(ok)
        failed += int(not ok)

    print()
    print("=" * 80)
    print(f"  PASSED: {passed}/{len(TEST_CASES)}")
    print(f"  FAILED: {failed}/{len(TEST_CASES)}")
    print("=" * 80)
    print()

    # Detail dump for any failures + a few representative passes
    for label, prompt, run, ok, issues in rows:
        marker = "PASS" if ok else "FAIL"
        print(f"\n[{marker}] [{label}] {prompt}")
        for name, args in run.tool_calls:
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            print(f"    -> {name}({args_str})")
        if run.duplicate_count:
            print(f"    (collapsed {run.duplicate_count} duplicate tool call(s))")
        if run.final_answer:
            preview = run.final_answer.replace("\n", " ")[:200]
            print(f"    >> {preview}{'...' if len(run.final_answer) > 200 else ''}")
        if issues:
            for i in issues:
                print(f"    !! {i}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
