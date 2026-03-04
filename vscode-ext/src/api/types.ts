// ── API request/response types (mirrors api/models/) ──

export interface QueryRequest {
  query: string;
  language?: string;
  product?: string;
  include_sources?: boolean;
  include_phases?: boolean;
}

export interface SourceInfo {
  document: string;
  page: number | null;
  score: number;
  type: string;
}

export interface UsageInfo {
  total_time_ms: number;
  phases_executed: number[];
  fallback_used: boolean;
  phase_times?: Record<string, number>;
}

export interface QueryResponse {
  success: boolean;
  answer: string;
  language: string;
  confidence: number;
  intent: string;
  product: string;
  sources?: SourceInfo[];
  usage: UsageInfo;
}

export interface ServiceStatus {
  status: string;
  latency_ms: number;
}

export interface HealthResponse {
  status: string;
  services: Record<string, ServiceStatus>;
  version: string;
}

export interface ProductInfo {
  id: string;
  name: string;
  keywords: string[];
}

export interface ProductsResponse {
  products: ProductInfo[];
}

// ── SSE event data shapes ──

export interface PhaseEventData {
  phase: number;
  name: string;
  status: string;
  time_ms: number;
}

export interface AnswerEventData {
  answer: string;
  confidence: number;
  language: string;
  intent: string;
  product: string;
  sources?: SourceInfo[];
}

export interface DoneEventData {
  total_time_ms: number;
}

export interface ErrorEventData {
  error: string;
}

// ── Webview ↔ Extension message protocol ──

export type ExtensionToWebviewMessage =
  | { type: "health"; data: HealthResponse }
  | { type: "products"; data: ProductInfo[] }
  | { type: "settings"; data: { language: string; product: string } }
  | { type: "streamPhase"; data: PhaseEventData }
  | { type: "streamAnswer"; data: AnswerEventData }
  | { type: "streamDone"; data: DoneEventData }
  | { type: "error"; message: string }
  | { type: "clearChat" };

export type WebviewToExtensionMessage =
  | { type: "ready" }
  | { type: "query"; query: string; language: string; product: string }
  | { type: "cancel" }
  | { type: "openSettings" };
