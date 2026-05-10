"""Westbridge AI Assistant — Barbara Prototype.

Streamlit chat UI on top of a Groq + Llama 3.3 70B agent loop with 9 tools
that read mock Rent Manager + QuickBooks data shaped like the real APIs.

Run: `streamlit run app.py`
"""

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from groq import BadRequestError, Groq

from tools import TOOL_SCHEMAS, execute_tool

CONFIRMATION_WORDS = (
    "yes", "yeah", "yep", "confirm", "confirmed", "proceed",
    "ok", "okay", "approve", "approved", "go ahead", "do it",
    "execute", "send it", "make it",
)


def user_has_confirmed(messages: list[dict]) -> bool:
    """True iff the most recent user turn looks like an explicit confirmation
    AND there's been at least one prior user turn (the original ask)."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if len(user_msgs) < 2:
        return False
    last = (user_msgs[-1].get("content") or "").lower().strip()
    return any(w in last for w in CONFIRMATION_WORDS)

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

MAX_TOOL_ITERATIONS = 8


# ─── Page config ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Westbridge AI Assistant",
    page_icon="🏢",
    layout="centered",
)

st.markdown(
    """
    <style>
        .block-container { max-width: 820px; }
        .tool-call {
            font-size: 0.82rem;
            color: #555;
            background: #f4f4f4;
            border-left: 3px solid #888;
            padding: 6px 10px;
            margin: 4px 0 10px 0;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, monospace;
        }
        .stChatMessage { padding: 8px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🏢 Westbridge AI Assistant")
    st.caption("Barbara — prototype v0.1")
    st.divider()

    if not GROQ_API_KEY:
        st.error("`GROQ_API_KEY` not set. Copy `.env.example` to `.env` and add your key.")
    else:
        st.success(f"Connected · {GROQ_MODEL}")

    st.divider()
    st.markdown("**Try one of these:**")
    sample_prompts = [
        "Any data quality issues across the portfolio?",
        "What's our occupancy at Riverdale Heights?",
        "Show me unpaid invoices over 30 days.",
        "What's the balance on Maria Cologne?",
        "Pay invoice 4729 from the Williamsburg account.",
        "List bank accounts for the 165 East LLC.",
    ]
    for prompt in sample_prompts:
        if st.button(prompt, use_container_width=True, key=f"sample_{prompt}"):
            st.session_state["pending_user_input"] = prompt

    st.divider()
    if st.button("🔄 Clear conversation", use_container_width=True):
        st.session_state.pop("messages", None)
        st.session_state.pop("display_log", None)
        st.rerun()

    st.divider()
    st.caption(
        "**Note:** This is a prototype. All data is mocked. "
        "No real money moves. Day 1 of employment, the data source flips "
        "from JSON files to live Rent Manager + QuickBooks APIs."
    )


# ─── Header ─────────────────────────────────────────────────────────────
st.title("🏢 Westbridge AI Assistant")
st.caption("Conversational ops, powered by Llama 3.3 70B on Groq")


# ─── Source Data Viewer (top of page so it stays reachable) ────────────
with st.expander("📊 View source data — what Barbara is reading from", expanded=False):
    st.caption(
        "All data is **mocked** for this demo. Day 1 of work, the data source flips "
        "from these JSON files to live Rent Manager + QuickBooks APIs — same code, "
        "same tools, same agent. Use this view to verify Barbara's answers against "
        "ground truth."
    )

    DATA_FILES = [
        ("Properties (5)", "properties"),
        ("Tenants (12)", "tenants"),
        ("Leases (12)", "leases"),
        ("Charges (17)", "charges"),
        ("Invoices (9)", "invoices"),
        ("Bank Accounts (7)", "bank_accounts"),
        ("Maintenance (5)", "service_issues"),
    ]

    tabs = st.tabs([label for label, _ in DATA_FILES])
    data_dir = Path(__file__).parent / "mock_data"
    for tab, (_, fname) in zip(tabs, DATA_FILES):
        with tab:
            data_path = data_dir / f"{fname}.json"
            with open(data_path, encoding="utf-8") as f:
                data = json.load(f)
            st.caption(f"`mock_data/{fname}.json` · {len(data)} records")
            st.dataframe(data, use_container_width=True, hide_index=True)

st.divider()


# ─── Session state ──────────────────────────────────────────────────────
# `messages` is the LLM-facing conversation history (system + user + assistant + tool).
# `display_log` is what we render in the UI — user messages, assistant text, and tool-call tags.
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "display_log" not in st.session_state:
    st.session_state.display_log = []


# ─── Render existing conversation ───────────────────────────────────────
for entry in st.session_state.display_log:
    if entry["kind"] == "user":
        with st.chat_message("user"):
            st.markdown(entry["content"])
    elif entry["kind"] == "assistant":
        with st.chat_message("assistant"):
            st.markdown(entry["content"])
    elif entry["kind"] == "tool_call":
        with st.chat_message("assistant"):
            st.markdown(
                f'<div class="tool-call">🔧 <b>{entry["name"]}</b>('
                f'{entry["args_summary"]})</div>',
                unsafe_allow_html=True,
            )


# ─── Agent loop ─────────────────────────────────────────────────────────
def run_agent(user_message: str) -> None:
    """Append user message, run the agent loop, render assistant turns."""
    if not GROQ_API_KEY:
        st.error("Set GROQ_API_KEY in `.env` to use the assistant.")
        return

    client = Groq(api_key=GROQ_API_KEY)

    st.session_state.messages.append({"role": "user", "content": user_message})
    st.session_state.display_log.append({"kind": "user", "content": user_message})
    with st.chat_message("user"):
        st.markdown(user_message)

    for _ in range(MAX_TOOL_ITERATIONS):
        # Try once; smaller models occasionally emit malformed tool args
        # that Groq rejects with 400. Retry transparently before failing.
        response = None
        for attempt in range(2):
            try:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=st.session_state.messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=0.2,
                )
                break
            except BadRequestError:
                if attempt == 0:
                    continue
                err = (
                    "⚠️ Couldn't process that one — the model misformatted a tool "
                    "call. Try rephrasing, or ask a different question."
                )
                st.session_state.display_log.append({"kind": "assistant", "content": err})
                with st.chat_message("assistant"):
                    st.warning(err)
                return
            except Exception as e:
                err = f"⚠️ Groq API error: {type(e).__name__}: {e}"
                st.session_state.display_log.append({"kind": "assistant", "content": err})
                with st.chat_message("assistant"):
                    st.error(err)
                return
        if response is None:
            return

        msg = response.choices[0].message
        # Append the raw assistant message (including any tool_calls) to history
        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
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
        st.session_state.messages.append(assistant_entry)

        if not msg.tool_calls:
            # Final answer
            final_text = msg.content or "(no content)"
            st.session_state.display_log.append({"kind": "assistant", "content": final_text})
            with st.chat_message("assistant"):
                st.markdown(final_text)
            return

        # Group identical tool calls (Llama 3.3 occasionally emits duplicates).
        # Execute each unique (name, arguments) pair ONCE and re-use its result
        # for every duplicate tool_call_id the model issued.
        groups: dict[tuple[str, str], list] = {}
        for tc in msg.tool_calls:
            key = (tc.function.name, tc.function.arguments or "{}")
            groups.setdefault(key, []).append(tc)

        for (name, args_json), tcs in groups.items():
            try:
                args = json.loads(args_json)
            except json.JSONDecodeError:
                args = {}
            # Some smaller models return JSON `null` or non-object values for
            # tools with all-optional parameters. Normalize to empty dict so
            # both `.items()` (UI display) and `**args` (tool dispatch) work.
            if not isinstance(args, dict):
                args = {}
            args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "no args"
            if len(tcs) > 1:
                args_summary += f"  ↺ collapsed {len(tcs)} duplicate calls"

            st.session_state.display_log.append({
                "kind": "tool_call",
                "name": name,
                "args_summary": args_summary,
            })
            with st.chat_message("assistant"):
                st.markdown(
                    f'<div class="tool-call">🔧 <b>{name}</b>({args_summary})</div>',
                    unsafe_allow_html=True,
                )

            # Execute once per unique call.
            # CRITICAL SAFETY GUARD: execute_payment can ONLY run if the user
            # has explicitly confirmed in a SEPARATE turn from the original
            # request. The model is told this in the system prompt, but smaller
            # models can ignore the instruction — this is the hard backstop.
            if name == "execute_payment" and not user_has_confirmed(st.session_state.messages):
                result = {
                    "blocked": True,
                    "error": (
                        "execute_payment is BLOCKED. The user has not confirmed "
                        "this draft. You must reply to the user with the draft "
                        "details and ASK them to type 'yes' or 'confirm'. "
                        "Only after they reply with confirmation may you call "
                        "execute_payment in a new turn."
                    ),
                }
            else:
                result = execute_tool(name, args)
            result_json = json.dumps(result, default=str)

            # The OpenAI/Groq schema requires one tool result per tool_call_id,
            # so we append the same result under every duplicate id.
            for tc in tcs:
                st.session_state.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": result_json,
                })

    # Hit the iteration cap
    msg_text = "⚠️ Reached max tool iterations without final answer."
    st.session_state.display_log.append({"kind": "assistant", "content": msg_text})
    with st.chat_message("assistant"):
        st.warning(msg_text)


# ─── Input handling ─────────────────────────────────────────────────────
pending = st.session_state.pop("pending_user_input", None)
typed = st.chat_input("Ask Barbara anything — try the prompts in the sidebar...")

user_input = pending or typed
if user_input:
    run_agent(user_input)
