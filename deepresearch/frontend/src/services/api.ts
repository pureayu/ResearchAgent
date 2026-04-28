const baseURL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export interface ResearchRequest {
  topic: string;
  session_id?: string;
  search_api?: string;
}

export interface ResearchStreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface ResearchRouteResponse {
  session_id: string;
  response_mode: "memory_recall" | "direct_answer" | "deep_research";
  confidence: number;
  reason: string;
  has_recallable_history: boolean;
}

export interface StreamOptions {
  signal?: AbortSignal;
}

export interface ProjectCreateRequest {
  project_id?: string;
  topic: string;
  selected_idea?: string;
}

export interface ProjectStatus {
  project_id: string;
  topic: string;
  stage: string;
  selected_idea: string;
  contract_path: string;
  experiment_plan_path: string;
  experiment_tracker_path: string;
  baseline: string;
  current_branch: string;
  training_status: string;
  active_tasks: string[];
  next_action: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectSnapshot {
  project_id: string;
  root_path: string;
  status: ProjectStatus;
  files: Record<string, string>;
}

export interface ProjectStatusPatch {
  stage?: string;
  selected_idea?: string;
  baseline?: string;
  current_branch?: string;
  training_status?: string;
  active_tasks?: string[];
  next_action?: string;
}

export interface IdeaCandidate {
  title: string;
  problem: string;
  hypothesis: string;
  minimum_viable_experiment: string;
  expected_outcome: string;
  method_sketch: string;
  expected_signal: string;
  novelty_risk: string;
  feasibility: string;
  impact: string;
  risk_level: "low" | "medium" | "high" | "unclear";
  contribution_type: "empirical" | "method" | "system" | "theory" | "diagnostic" | "unclear";
  ranking_rationale: string;
  estimated_effort: string;
  reviewer_objection: string;
  why_do_this: string;
  pilot_signal: "not_run" | "positive" | "weak_positive" | "negative" | "skipped";
  required_experiments: string[];
  score: number;
  closest_related_work: string[];
  overlap_analysis: string;
  novelty_claim: string;
  novelty_verdict: "novel" | "incremental" | "overlapping" | "unclear";
  novelty_confidence: number;
}

export interface IdeaDiscoveryRequest {
  report_markdown?: string;
  run_research?: boolean;
  auto_select_top?: boolean;
  use_structured_extraction?: boolean;
  use_project_graph?: boolean;
  enable_novelty_check?: boolean;
  selected_candidate_title?: string;
  selected_candidate_index?: number;
}

export interface IdeaDiscoveryResult {
  project_id: string;
  report_markdown: string;
  selected_idea: IdeaCandidate | null;
  candidates: IdeaCandidate[];
  snapshot: ProjectSnapshot;
}

export interface DirectionRefinementResult {
  project_id: string;
  original_idea: IdeaCandidate;
  refined_idea: IdeaCandidate;
  snapshot: ProjectSnapshot;
}

export interface ExternalReviewRequest {
  review_text?: string;
  verdict?: string;
  max_rounds?: number;
  use_external_model?: boolean;
}

export interface ExternalReviewOutput {
  verdict: "positive" | "needs_revision" | "reject" | "unclear";
  summary: string;
  strengths: string[];
  weaknesses: string[];
  action_items: string[];
  raw_review: string;
}

export interface ExternalReviewResult {
  project_id: string;
  round: number;
  status: string;
  review: ExternalReviewOutput;
  snapshot: ProjectSnapshot;
}

export interface ExperimentBridgeRequest {
  sanity_first?: boolean;
}

export interface ExperimentTask {
  id: string;
  title: string;
  goal: string;
  command: string;
  expected_signal: string;
  status: string;
}

export interface ExperimentBridgeResult {
  project_id: string;
  tasks: ExperimentTask[];
  snapshot: ProjectSnapshot;
}

async function requestJson<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(`${baseURL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw new Error(errorText || `请求失败，状态码：${response.status}`);
  }

  return (await response.json()) as T;
}

export async function createProject(
  payload: ProjectCreateRequest
): Promise<ProjectSnapshot> {
  return requestJson<ProjectSnapshot>("/projects", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function updateProject(
  projectId: string,
  payload: ProjectStatusPatch
): Promise<ProjectSnapshot> {
  return requestJson<ProjectSnapshot>(
    `/projects/${encodeURIComponent(projectId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload)
    }
  );
}

export async function runProjectIdeaDiscovery(
  projectId: string,
  payload: IdeaDiscoveryRequest
): Promise<IdeaDiscoveryResult> {
  return requestJson<IdeaDiscoveryResult>(
    `/projects/${encodeURIComponent(projectId)}/idea-discovery`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export async function runDirectionRefinement(
  projectId: string
): Promise<DirectionRefinementResult> {
  return requestJson<DirectionRefinementResult>(
    `/projects/${encodeURIComponent(projectId)}/direction-refine`,
    {
      method: "POST"
    }
  );
}

export async function runExternalReview(
  projectId: string,
  payload: ExternalReviewRequest
): Promise<ExternalReviewResult> {
  return requestJson<ExternalReviewResult>(
    `/projects/${encodeURIComponent(projectId)}/external-review`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export async function runExperimentBridge(
  projectId: string,
  payload: ExperimentBridgeRequest
): Promise<ExperimentBridgeResult> {
  return requestJson<ExperimentBridgeResult>(
    `/projects/${encodeURIComponent(projectId)}/experiment-bridge`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export async function classifyResearchRoute(
  payload: ResearchRequest
): Promise<ResearchRouteResponse> {
  return requestJson<ResearchRouteResponse>("/research/route", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function runResearchStream(
  payload: ResearchRequest,
  onEvent: (event: ResearchStreamEvent) => void,
  options: StreamOptions = {}
): Promise<void> {
  const response = await fetch(`${baseURL}/research/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream"
    },
    body: JSON.stringify(payload),
    signal: options.signal
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw new Error(
      errorText || `研究请求失败，状态码：${response.status}`
    );
  }

  const body = response.body;
  if (!body) {
    throw new Error("浏览器不支持流式响应，无法获取研究进度");
  }

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);

      if (rawEvent.startsWith("data:")) {
        const dataPayload = rawEvent.slice(5).trim();
        if (dataPayload) {
          try {
            const event = JSON.parse(dataPayload) as ResearchStreamEvent;
            onEvent(event);

            if (event.type === "error" || event.type === "done") {
              return;
            }
          } catch (error) {
            console.error("解析流式事件失败：", error, dataPayload);
          }
        }
      }

      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      // 处理可能的尾巴事件
      if (buffer.trim()) {
        const rawEvent = buffer.trim();
        if (rawEvent.startsWith("data:")) {
          const dataPayload = rawEvent.slice(5).trim();
          if (dataPayload) {
            try {
              const event = JSON.parse(dataPayload) as ResearchStreamEvent;
              onEvent(event);
            } catch (error) {
              console.error("解析流式事件失败：", error, dataPayload);
            }
          }
        }
      }
      break;
    }
  }
}
