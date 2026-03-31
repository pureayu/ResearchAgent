# DeepResearchAgent Refactor Plan

## Purpose

This note captures the current understanding of `deepresearch/backend/src/agent.py`
and a safe refactor order for the next stage of development.

The immediate goal is not to add new features first. The goal is to make the current
agent orchestration easier to understand, easier to extend, and safer to modify.


## Current Reality

`DeepResearchAgent` currently mixes three different responsibilities inside `__init__`:

1. Infrastructure setup
   - config
   - shared LLM client
   - memory service
   - note tool
   - tool registry
   - tool tracker

2. Role-agent creation
   - `todo_agent`
   - `report_agent`
   - `review_agent`
   - `direct_answer_agent`
   - `summarizer_factory`

3. Service wiring
   - `PlanningService`
   - `SummarizationService`
   - `ReportingService`
   - `ReviewerService`

At runtime, `run()` and `run_stream()` also each contain several mixed stages:

1. Build initial state
2. Create or reuse session
3. Start run
4. Capture profile memory
5. Recall memory context
6. Classify response mode
7. Initialize tasks
8. Execute one or more research rounds
9. Review coverage and append follow-up tasks
10. Generate final output
11. Persist report and semantic memory


## Important Clarification

We discussed an important distinction:

- `agent creation` is about building the role-specific `ToolAwareSimpleAgent` objects.
- `runtime bootstrap` is about preparing the `SummaryState` for a single request.

These are not the same thing.

For that reason, if the next development focus is "agent-related code", the first refactor
should prioritize:

1. extracting role-agent creation
2. extracting service wiring

And only after that:

3. extracting runtime bootstrap


## Recommended Refactor Order

### Phase 1: Separate Construction Responsibilities

First, make `DeepResearchAgent.__init__` easier to read by splitting it into explicit build steps.

Suggested private methods:

- `_build_infrastructure()`
- `_build_role_agents()`
- `_build_services()`

Suggested intent:

- `_build_infrastructure()`
  - initialize `llm`
  - initialize `memory_service`
  - initialize `note_tool`
  - initialize `tool_registry`
  - initialize `tool_tracker`

- `_build_role_agents()`
  - create planner/reporter/reviewer/direct-answer agents
  - create summarizer factory

- `_build_services()`
  - create `PlanningService`
  - create `SummarizationService`
  - create `ReportingService`
  - create `ReviewerService`

This phase should not change runtime behavior.


### Phase 2: Separate Runtime Bootstrap

Only after Phase 1 is stable, extract the shared setup logic used by both `run()` and `run_stream()`.

Suggested method:

- `_bootstrap_state(topic: str, session_id: str | None) -> SummaryState`

This method should own:

- create `SummaryState`
- resolve session
- start run
- capture profile memory
- load recalled context
- classify `response_mode`

This is runtime initialization, not agent creation.


### Phase 3: Separate Task Initialization

After bootstrap is extracted, split response-mode-specific task initialization.

Suggested method:

- `_initialize_tasks_for_mode(state: SummaryState) -> None`

This method should handle:

- memory recall synthetic task
- direct answer synthetic task
- deep research planner tasks
- fallback task creation
- round metadata initialization


### Phase 4: Separate Round Execution

Once initialization is cleaner, move the multi-round orchestration into clearer units.

Suggested methods:

- `_run_round(...)`
- `_review_round(...)`
- `_finalize_run(...)`

Expected responsibilities:

- `_run_round(...)`
  - iterate over pending tasks
  - call `_execute_task(...)`
  - emit streaming task events when needed

- `_review_round(...)`
  - call `ReviewerService.review_progress(...)`
  - decide whether follow-up tasks should be appended

- `_finalize_run(...)`
  - build final report or direct output
  - persist note/report memory
  - consolidate semantic facts when applicable


## Suggested Internal Shape

One practical direction is to introduce lightweight containers so the construction logic is explicit.

Possible examples:

- `AgentInfrastructure`
- `RoleAgents`
- `AgentServices`

This is optional, but it can make `DeepResearchAgent` much easier to read.

Example shape:

- `AgentInfrastructure`
  - `llm`
  - `memory_service`
  - `note_tool`
  - `tools_registry`
  - `tool_tracker`

- `RoleAgents`
  - `todo_agent`
  - `report_agent`
  - `review_agent`
  - `direct_answer_agent`
  - `summarizer_factory`

- `AgentServices`
  - `planner`
  - `summarizer`
  - `reporting`
  - `reviewer`


## Guardrails

The refactor should preserve behavior while improving structure.

Rules to follow:

1. Do not change response-mode routing behavior during Phase 1.
2. Do not change reviewer loop behavior during Phase 1.
3. Do not change stream event names during Phase 1.
4. Do not change memory persistence timing during Phase 1.
5. Refactor in small steps so `run()` and `run_stream()` keep matching behavior.


## What Not To Do First

Do not start with these before construction and orchestration are cleaner:

- a new `ContextManager`
- a new global reflection framework
- a new multi-agent hierarchy
- prompt redesign across all roles
- changing memory ownership again

Those changes may still be valuable, but they should come after `DeepResearchAgent`
has clearer internal boundaries.


## Next Concrete Step

The best first coding step is:

1. extract role-agent creation from `__init__`
2. extract service wiring from `__init__`

Only after that should we decide whether to:

- continue with `_bootstrap_state()`
- continue with `_initialize_tasks_for_mode()`
- or move on to context management


## Why This Order

This order keeps the work aligned with the actual intent:

- if we say we are working on "agent-related code", we should first clean up
  how agents are created and wired
- if we later work on "runtime workflow", then we clean up bootstrap, rounds,
  review, and finalization

That separation makes future changes much safer.
