# ARIS Minimal Alignment

This backend now has a minimal ARIS-style project workspace layer. It is intentionally separate from the existing `DeepResearchAgent` execution path so the LangGraph migration remains stable.

## Mechanism

- `PROJECT_STATUS.json` is the canonical machine-readable project state.
- `CLAUDE.md` mirrors the same status in the `## Pipeline Status` format used by ARIS-style sessions.
- `docs/research_contract.md` stores the selected active idea and claim-level contract.
- `refine-logs/EXPERIMENT_PLAN.md` and `refine-logs/EXPERIMENT_TRACKER.md` define the experiment handoff surface.
- `EXPERIMENT_LOG.md`, `AUTO_REVIEW.md`, `REVIEW_STATE.json`, `IDEA_REPORT.md`, `IDEA_CANDIDATES.md`, and `findings.md` reserve the remaining lifecycle files.

## API

- `POST /projects` creates a workspace and all protocol files.
- `GET /projects/{project_id}` loads the project snapshot.
- `PATCH /projects/{project_id}` updates allowed status fields and refreshes `CLAUDE.md`.
- `POST /projects/{project_id}/idea-discovery` writes `IDEA_REPORT.md`, extracts `IDEA_CANDIDATES.md` / `IDEA_CANDIDATES.json`, auto-selects the top candidate by default, and refreshes the active contract and experiment plan.

## Current Idea Discovery Behavior

- If `report_markdown` is provided, the endpoint uses it directly. This keeps smoke tests independent from LLM and search dependencies.
- If `run_research=true`, the endpoint calls the existing `DeepResearchAgent` for the project topic, then persists the report.
- Candidate extraction tries LangChain structured output when `use_structured_extraction=true`.
- If structured extraction is unavailable or fails, candidate extraction falls back to deterministic parsing over headings and bullets.
- If `enable_novelty_check=true`, candidates are annotated with `closest_related_work`, `overlap_analysis`, `novelty_claim`, `novelty_verdict`, and `novelty_confidence`.
- The novelty checker tries arXiv metadata plus structured LLM assessment when dependencies and model configuration are available. Otherwise it writes an explicit `unclear` verdict and pending search query.
- If `use_project_graph=true`, the endpoint routes through the project-level LangGraph when `langgraph` is installed. Otherwise it falls back to the plain service path.
- Idea selection supports explicit `selected_candidate_title`, explicit 1-based `selected_candidate_index`, automatic ranked selection, or human-gate pause with `auto_select_top=false`.

## Review And Experiment Bridge

- `POST /projects/{project_id}/external-review` appends one review round to `AUTO_REVIEW.md`, updates `REVIEW_STATE.json`, and moves project status to `auto_review`.
- If `review_text` is omitted and `use_external_model=true`, the external review endpoint calls the configured LangChain/OpenAI-compatible model and stores the structured verdict.
- To use a real hosted reviewer, configure `LLM_PROVIDER=custom`, `LLM_MODEL_ID`, `LLM_BASE_URL`, and `LLM_API_KEY`. `LLM_PROVIDER=openai` also works when the default OpenAI endpoint is intended.
- `POST /projects/{project_id}/experiment-bridge` converts the selected idea into `EXPERIMENT_TRACKER.md` tasks, appends `EXPERIMENT_LOG.md`, and moves project status to `experiment_bridge`.
- These stages are durable file/API stages. They do not yet launch jobs. External review can call a real model, but it currently shares the backend LLM config rather than a separate reviewer-only config.

## Next Integration Step

The next layer should add a project-level LangGraph whose nodes update this workspace:

1. Add reviewer-specific env vars so the external reviewer can intentionally use a different model/provider from the main generator.
2. Extend the project-level LangGraph so external review and experiment bridge can be graph nodes, not only API/service stages.
3. Add `run_experiment`, watchdog, and monitor nodes that append to the durable logs.
