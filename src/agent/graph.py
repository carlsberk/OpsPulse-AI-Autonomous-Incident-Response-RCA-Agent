"""Compiled LangGraph workflow with a bounded self-correction loop.

normalize_incident -> extract_stack_trace_context -> retrieve_source_context -> retrieve_historical_incidents
    -> analyze_root_cause -> evaluate_analysis -> (retry: analyze_root_cause | END)

Termination: every pass through `analyze_root_cause` increments `iterations`, and the router only loops
while `iterations < MAX_ANALYSIS_ITERATIONS`, so at most that many LLM calls are made per incident.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import (
    ContextFetcher,
    HistoryLookup,
    evaluate_analysis,
    extract_stack_trace_context,
    make_analyze_root_cause,
    make_retrieve_historical_incidents,
    make_retrieve_source_context,
    normalize_incident,
)
from src.agent.state import IncidentState
from src.config import settings
from src.integrations.llm import ModelFactory


def route_after_analysis(state: IncidentState) -> str:
    return END if state["workflow_status"] == "failed" else "evaluate_analysis"


def route_after_evaluation(state: IncidentState) -> str:
    if state["workflow_status"] == "running" and state["iterations"] < settings.max_analysis_iterations:
        return "analyze_root_cause"
    return END


def build_graph(
    fetch_context: ContextFetcher, find_history: HistoryLookup, model_factory: ModelFactory | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph: StateGraph[IncidentState, None, IncidentState, IncidentState] = StateGraph(IncidentState)
    # (langgraph's add_node overloads cannot infer the state type of the factory-built closures, hence the ignores)
    graph.add_node("normalize_incident", normalize_incident)
    graph.add_node("extract_stack_trace_context", extract_stack_trace_context)
    graph.add_node("retrieve_source_context", make_retrieve_source_context(fetch_context))  # type: ignore[arg-type]
    graph.add_node("retrieve_historical_incidents", make_retrieve_historical_incidents(find_history))  # type: ignore[arg-type]
    graph.add_node("analyze_root_cause", make_analyze_root_cause(model_factory))  # type: ignore[arg-type]
    graph.add_node("evaluate_analysis", evaluate_analysis)

    graph.add_edge(START, "normalize_incident")
    graph.add_edge("normalize_incident", "extract_stack_trace_context")
    graph.add_edge("extract_stack_trace_context", "retrieve_source_context")
    graph.add_edge("retrieve_source_context", "retrieve_historical_incidents")
    graph.add_edge("retrieve_historical_incidents", "analyze_root_cause")
    graph.add_conditional_edges("analyze_root_cause", route_after_analysis)
    graph.add_conditional_edges("evaluate_analysis", route_after_evaluation)
    return graph.compile()
