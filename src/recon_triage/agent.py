"""LangGraph workflow that explains a break and proposes a resolution.

A single node per invocation: given one break plus the resolution policy, it
asks Claude for a structured Recommendation (explanation, likely root cause,
proposed resolution, recommended action). The answer is grounded only in the
break data and the policy document, and must reference specific fields. It must
never invent transactions.

If no Anthropic API key is configured, a deterministic rule-based fallback is
used so the dashboard and the demo still work offline. The fallback is clearly
labelled in its output.
"""

from __future__ import annotations

import os
from typing import Optional, TypedDict

from .schemas import Break, BreakType, Recommendation, RecommendedAction, Transaction

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are a reconciliation triage analyst in a clearing and settlement operation.
You are given exactly one reconciliation break and a short resolution policy.

Rules you must follow:
- Ground every statement in the break data and the policy you are shown.
- Reference specific transaction fields by name (for example reference, amount,
  currency, settlement_date, counterparty).
- Never invent transactions, amounts, or identifiers that are not present.
- Choose recommended_action from the allowed set only.
- When the policy covers this break type, cite the relevant section in
  sop_reference.
- Be concise and defensible. An auditor will read this.
"""


class AgentState(TypedDict, total=False):
    """State carried through the LangGraph workflow for a single break."""

    break_item: Break
    sop: str
    recommendation: Optional[Recommendation]


def _txn_summary(label: str, txn: Optional[Transaction]) -> str:
    if txn is None:
        return f"{label}: (none on this side)"
    return (
        f"{label}: txn_id={txn.txn_id}, reference={txn.reference}, "
        f"amount={txn.amount}, currency={txn.currency}, "
        f"settlement_date={txn.settlement_date.isoformat()}, "
        f"counterparty={txn.counterparty}"
    )


def _render_break(break_item: Break) -> str:
    return (
        f"break_id: {break_item.break_id}\n"
        f"break_type: {break_item.break_type.value}\n"
        f"confidence: {break_item.confidence}\n"
        f"detail: {break_item.detail}\n"
        f"{_txn_summary('ledger_txn', break_item.ledger_txn)}\n"
        f"{_txn_summary('bank_txn', break_item.bank_txn)}"
    )


def _heuristic_recommendation(break_item: Break) -> Recommendation:
    """Rule-based fallback used when no LLM is available.

    Mirrors the resolution policy so the offline demo stays coherent.
    """

    mapping = {
        BreakType.MISSING_IN_BANK: (
            RecommendedAction.AWAIT_SETTLEMENT,
            "The ledger recorded a payment the bank has not yet reported.",
            "Confirm the entry is within the settlement window, then await the "
            "bank credit before resolving.",
            "Policy 1: Missing in bank",
        ),
        BreakType.MISSING_IN_LEDGER: (
            RecommendedAction.MANUAL_REVIEW,
            "The bank reported a movement with no matching ledger entry.",
            "Identify the originating instruction and book the ledger entry, or "
            "escalate if no instruction exists.",
            "Policy 2: Missing in ledger",
        ),
        BreakType.AMOUNT_MISMATCH: (
            RecommendedAction.POST_ADJUSTMENT,
            "The amounts differ by a fee-sized margin, consistent with a "
            "deducted charge.",
            "Post an adjustment for the fee difference and reconcile the net.",
            "Policy 3: Amount mismatch",
        ),
        BreakType.DATE_LAG: (
            RecommendedAction.AWAIT_SETTLEMENT,
            "Amount and currency agree; only the settlement date differs, "
            "consistent with T+1 or T+2 settlement.",
            "Await settlement on the lagged side; no adjustment is required.",
            "Policy 4: Date lag",
        ),
        BreakType.DUPLICATE: (
            RecommendedAction.WRITE_OFF,
            "The same line appears more than once on one book.",
            "Reverse the duplicate entry and retain the original.",
            "Policy 5: Duplicate",
        ),
        BreakType.CURRENCY_MISMATCH: (
            RecommendedAction.ESCALATE,
            "The amount matches but the currency code differs.",
            "Escalate to FX operations to confirm the correct settlement "
            "currency before any adjustment.",
            "Policy 6: Currency mismatch",
        ),
    }
    action, cause, resolution, sop = mapping[break_item.break_type]
    referenced = []
    if break_item.ledger_txn is not None:
        referenced.extend(["ledger_txn.amount", "ledger_txn.currency"])
    if break_item.bank_txn is not None:
        referenced.extend(["bank_txn.amount", "bank_txn.currency"])
    return Recommendation(
        explanation=(
            f"[rule-based fallback] {break_item.detail}"
        ),
        likely_root_cause=cause,
        proposed_resolution=resolution,
        recommended_action=action,
        referenced_fields=referenced or ["reference"],
        sop_reference=sop,
    )


def _call_claude(break_item: Break, sop: str) -> Recommendation:
    """Ask Claude for a structured Recommendation for one break."""

    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    user_content = (
        "Resolution policy:\n"
        f"{sop}\n\n"
        "Reconciliation break to triage:\n"
        f"{_render_break(break_item)}\n\n"
        "Return a single structured recommendation grounded in the data above."
    )

    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": user_content}],
        output_format=Recommendation,
    )
    parsed = response.parsed_output
    if parsed is None:
        # The model refused or did not produce schema-valid output; fall back
        # rather than fabricate.
        return _heuristic_recommendation(break_item)
    return parsed


def generate_recommendation(break_item: Break, sop: str) -> Recommendation:
    """Produce a recommendation for a break, using Claude when configured."""

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _call_claude(break_item, sop)
        except Exception:
            # Network or API failure must not take down triage; degrade to the
            # rule-based path and keep the audit trail honest.
            return _heuristic_recommendation(break_item)
    return _heuristic_recommendation(break_item)


def _recommend_node(state: AgentState) -> AgentState:
    """The single workflow node: explain and recommend for one break."""

    recommendation = generate_recommendation(state["break_item"], state.get("sop", ""))
    return {"recommendation": recommendation}


def build_agent():
    """Build and compile the LangGraph workflow.

    Kept as a one-node graph on purpose: the deterministic matcher does the
    heavy lifting, and the agent's job is a single grounded explanation per
    break. The graph structure leaves room to add review or verification nodes
    later without changing the call site.
    """

    from langgraph.graph import END, StateGraph

    graph = StateGraph(AgentState)
    graph.add_node("recommend", _recommend_node)
    graph.set_entry_point("recommend")
    graph.add_edge("recommend", END)
    return graph.compile()


# Compiled lazily so importing this module never requires langgraph at import
# time (useful for unit tests that mock the agent).
_compiled_agent = None


def run_agent(break_item: Break, sop: str) -> Recommendation:
    """Run the compiled LangGraph workflow for a single break."""

    global _compiled_agent
    if _compiled_agent is None:
        _compiled_agent = build_agent()
    result = _compiled_agent.invoke({"break_item": break_item, "sop": sop})
    return result["recommendation"]
