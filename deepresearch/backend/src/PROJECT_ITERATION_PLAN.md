# Project Iteration Plan

## Goal

Align ResearchAgent with the ARIS workflow while keeping the existing LangGraph deep-research path stable.

## Iteration Order

1. Project-level idea discovery loop.
   - Create or load a project workspace.
   - Run deep research for the topic.
   - Write `IDEA_REPORT.md`.
   - Extract 3-5 structured idea candidates.
   - Write `IDEA_CANDIDATES.md` and `IDEA_CANDIDATES.json`.
   - Auto-select the top candidate as a temporary default.
   - Refresh `docs/research_contract.md` and `refine-logs/EXPERIMENT_PLAN.md`.

2. Structured idea model.
   - Use `IdeaCandidate` as the API and file schema.
   - Keep Markdown files human/agent-readable.
   - Keep JSON files canonical for backend routing.
   - Prefer LangChain structured output and fall back to deterministic extraction.

3. Novelty check.
   - For each candidate, search related work.
   - Add closest work, overlap, difference, verdict, and confidence.
   - Update both Markdown and JSON candidate files.

4. External review loop.
   - Add a reviewer client abstraction.
   - Save raw reviews to `AUTO_REVIEW.md`.
   - Save loop state to `REVIEW_STATE.json`.
   - Only then allow automatic candidate refinement.

5. Experiment bridge.
   - Read `research_contract.md` and `EXPERIMENT_PLAN.md`.
   - Generate `EXPERIMENT_TRACKER.md` tasks.
   - Add sanity commands before any full experiment deployment.

## Implemented So Far

- Stage 1 is implemented as a minimal file-backed loop.
- `IdeaCandidate` and `IDEA_CANDIDATES.json` are implemented.
- Candidate extraction now supports LangChain structured output when dependencies and model configuration are available.
- Deterministic extraction remains as fallback for smoke tests and dependency-light environments.
- A project-level LangGraph route exists for `load_project -> resolve_report -> extract_candidates -> novelty_check -> persist_outputs`.
- `novelty_check` is implemented as an optional node. With dependencies configured it can use arXiv metadata plus structured LLM assessment; otherwise it records an explicit `unclear` verdict and pending search query.
- `select_idea_gate` is implemented. It supports explicit selection by title/index, automatic ranked selection, or human-gate pause when `auto_select_top=false`.
- External review persistence is implemented through `POST /projects/{project_id}/external-review`; it appends `AUTO_REVIEW.md` and updates `REVIEW_STATE.json`.
- External review can now call the configured LangChain/OpenAI-compatible model when `review_text` is omitted and `use_external_model=true`.
- Experiment bridge is implemented through `POST /projects/{project_id}/experiment-bridge`; it writes `EXPERIMENT_TRACKER.md`, appends `EXPERIMENT_LOG.md`, and marks experiments as planned.
- The backend `.venv` dependency gap has been fixed by installing the declared LangChain/LangGraph packages and repairing setuptools packaging metadata.

## Current Gaps

- External review uses the same backend LLM configuration (`LLM_PROVIDER`, `LLM_MODEL_ID`, `LLM_BASE_URL`, `LLM_API_KEY`) for the live reviewer. It still needs a deliberately separate external-model config if we want reviewer isolation from the main generator.
- Experiment bridge generates task plans and trackers, but does not modify code or launch jobs.
- Run/monitor experiment and watchdog integration are not implemented yet.
- The project-level graph is still limited to idea discovery, selection, and initial novelty annotation; external review and experiment bridge are API/service stages, not graph nodes yet.
