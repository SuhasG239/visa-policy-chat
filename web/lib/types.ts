// Mirrors the response models in app/api.py. Keep the two in sync.

export type DocType =
  | "regulation"
  | "policy_guidance"
  | "proposed_rule"
  | "final_rule"
  | "alert"
  | "court_order";

export interface Source {
  chunk_id: string;
  citation: string;
  title: string;
  url: string;
  source_updated: string | null;
  effective_date: string | null;
  doc_type: DocType | string;
  scope: string[];
  /** Court orders or alerts that may suspend or change this policy (sync rule 5). */
  affected_by?: string[];
}

/** POST /ask response, and the payload of the SSE `done` event. */
export interface AskResponse {
  text: string;
  sources: Source[];
  model: string;
  removed_links: string[];
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AskRequest {
  question: string;
  history: HistoryMessage[];
}

/** GET /meta response. */
export interface Meta {
  model: string;
  last_synced: string | null;
  chunk_count: number;
  scope: string;
  disclaimer: string;
}
