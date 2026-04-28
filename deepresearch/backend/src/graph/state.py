"""State definitions for the LangGraph-based deep research workflow."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

from models import SummaryState


class DeepResearchWorkflowState(TypedDict):
    """Execution state carried through the LangGraph workflow."""

    state: SummaryState
    streaming: bool
    current_round: NotRequired[int]
    max_rounds: NotRequired[int]
    step_counter: NotRequired[int]
    final_report: NotRequired[str | None]
    continue_research: NotRequired[bool]
