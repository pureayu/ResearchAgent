"""LangGraph builder for the deep research workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.state import DeepResearchWorkflowState


def build_deep_research_graph(agent: object):
    """Build the LangGraph workflow using bound methods on the agent."""

    builder = StateGraph(DeepResearchWorkflowState)

    builder.add_node("bootstrap", agent._graph_bootstrap_node)
    builder.add_node("execute_round", agent._graph_execute_round_node)
    builder.add_node("review_round", agent._graph_review_round_node)
    builder.add_node("generate_report", agent._graph_generate_report_node)
    builder.add_node("persist_outputs", agent._graph_persist_outputs_node)

    builder.add_edge(START, "bootstrap")
    builder.add_conditional_edges(
        "bootstrap",
        agent._route_after_bootstrap,
        {
            "execute_round": "execute_round",
            "generate_report": "generate_report",
        },
    )
    builder.add_conditional_edges(
        "execute_round",
        agent._route_after_execute_round,
        {
            "review_round": "review_round",
            "generate_report": "generate_report",
        },
    )
    builder.add_conditional_edges(
        "review_round",
        agent._route_after_review_round,
        {
            "execute_round": "execute_round",
            "generate_report": "generate_report",
        },
    )
    builder.add_edge("generate_report", "persist_outputs")
    builder.add_edge("persist_outputs", END)

    return builder.compile()
