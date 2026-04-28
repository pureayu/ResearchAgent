# Deep Research Benchmark V1

This directory contains the first gold-set benchmark for the capability-based deep research system.

## Purpose

The goal of this benchmark is not to be large. It is meant to be:

- small enough to inspect manually
- stable across refactors
- aligned with the current system capabilities
- useful for route, evidence, and report-quality evaluation

V1 is intentionally a hand-authored gold set.

## Case Schema

Each line in `cases.jsonl` is one JSON object with the following fields:

- `id`: stable case identifier
- `mode`: currently always `deep_research`
- `domain`: `academic`, `mixed`, or `insufficient`
- `language`: `zh`, `en`, or `mixed`
- `query`: the user-facing research query
- `expected_route_contains`: capability ids that should appear somewhere in `planned_capabilities`
- `expected_source_types`: source types that should appear in the final evidence when the task succeeds
- `expected_gap_reason`: expected terminal gap reason for failure-mode cases; otherwise `null`
- `required_features`: runtime features needed to run the case
- `reference_titles`: known relevant papers or repositories; useful for manual inspection
- `must_have_facts`: minimum facts the report should cover
- `must_have_keywords`: keywords that should appear in the final report or task summary
- `forbidden_patterns`: phrases that should not appear in a good answer
- `notes`: short explanation of why the case exists

## Authoring Rules

- Prefer concrete, answerable questions over vague topics.
- Keep one core evaluation goal per case.
- Use `expected_route_contains` for routing checks; do not encode exact route equality unless necessary.
- Use `must_have_facts` as short evaluation criteria, not as full reference answers.
- Put failure-mode queries into `domain = insufficient`; these should test whether the system fails honestly.
## Recommended V1 Mix

- `academic`: local-library and academic-paper questions
- `mixed`: questions that should combine academic and web evidence
- `insufficient`: impossible or unsupported requests that should terminate cleanly

The current starter set contains 4 cases:

- 4 academic

## Scripts

Run the benchmark:

```bash
cd deepresearch/backend
.venv/bin/python scripts/run_benchmark.py
```

Run a subset:

```bash
cd deepresearch/backend
.venv/bin/python scripts/run_benchmark.py --case-id acad_001 --case-id acad_002
```

Score a saved run:

```bash
cd deepresearch/backend
.venv/bin/python scripts/score_benchmark.py --results benchmarks/v1/results/run_<timestamp>.json
```

The runner writes one JSON file per run under `benchmarks/v1/results/`.
The scorer writes a companion `*_scored.json` file with per-case checks and aggregate rates.

## Next Steps

After the first scoring loop is in place, expand this set to roughly 30 cases:

- 14 academic
- 10 mixed
- 6 insufficient

Then add:

- automated route-match scoring
- source-coverage scoring
- keyword/fact coverage scoring
- ablation runs
