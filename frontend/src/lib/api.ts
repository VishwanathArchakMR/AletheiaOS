export type HealthState = "ok" | "unavailable";
export interface HealthResponse {
  status: HealthState;
  service: "api" | "falkordb";
}

export interface SourceInput {
  source_id: string;
  kind: "document" | "ticket" | "chat" | "pull_request" | "decision";
  file_format: "md" | "txt" | "json" | "csv";
  canonical_url?: string;
  content: string;
}

export interface IngestionReceipt {
  source_id: string;
  revision_id: string;
  chunks_processed: number;
  status: string;
}

export interface EvidenceMatch {
  source_id: string;
  kind: string;
  canonical_url: string | null;
  chunk_id: string;
  revision_id: string;
  citation_locator: string;
  content: string;
}

export type AnswerStatus =
  | "supported"
  | "conflicting"
  | "stale"
  | "ambiguous"
  | "insufficient_evidence"
  | "temporarily_unavailable";

export interface AnswerResponse {
  status: AnswerStatus;
  answer: string;
  claims: Array<Record<string, unknown>>;
  entities?: Array<Record<string, unknown>>;
}

export type GraphToolName =
  | "search_evidence"
  | "resolve_entities"
  | "who_owns"
  | "who_knows"
  | "why_decision"
  | "decision_lineage"
  | "impact"
  | "find_conflicts"
  | "find_stale_claims"
  | "how_related";

export interface GraphToolResponse {
  tool: GraphToolName;
  results: Array<Record<string, unknown>>;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function getHealth(path: string): Promise<HealthResponse> {
  const response = await fetch(`${baseUrl}/api/v1/health/${path}`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok && response.status !== 503) throw new Error("Unusable API status");
  return response.json() as Promise<HealthResponse>;
}

export const api = { live: () => getHealth("live"), ready: () => getHealth("ready") };

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${baseUrl}/api/v1/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const result = (await response.json()) as T & { detail?: string };
  if (!response.ok) {
    if (
      response.status === 503 &&
      "status" in result &&
      result.status === "temporarily_unavailable"
    ) {
      return result;
    }
    throw new Error(result.detail ?? "The request could not be completed.");
  }
  return result;
}

export const knowledgeApi = {
  ingestSource: (source: SourceInput) =>
    postJson<IngestionReceipt>("sources", source),
  searchEvidence: (query: string) =>
    postJson<{ matches: EvidenceMatch[] }>("evidence/search", { query, limit: 10 }),
  answerQuestion: (question: string) =>
    postJson<AnswerResponse>("answers", { question }),
  runTool: (tool: GraphToolName, parameters: Record<string, string | number>) =>
    postJson<GraphToolResponse>(`tools/${tool}`, parameters),
};
