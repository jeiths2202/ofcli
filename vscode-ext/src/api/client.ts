import {
  HealthResponse,
  ProductsResponse,
  QueryResponse,
  PhaseEventData,
  AnswerEventData,
  DoneEventData,
  ErrorEventData,
} from "./types";

export class APIError extends Error {
  constructor(message: string, public hint?: string) {
    super(message);
    this.name = "APIError";
  }
}

export class AuthenticationError extends APIError {
  constructor(message = "API key is invalid or missing") {
    super(message, "Open Settings to configure your API key");
    this.name = "AuthenticationError";
  }
}

export class ServerError extends APIError {
  constructor(message: string) {
    super(message, "Check server health with OFKMS: Check Health");
    this.name = "ServerError";
  }
}

export interface StreamCallbacks {
  onPhase: (data: PhaseEventData) => void;
  onAnswer: (data: AnswerEventData) => void;
  onDone: (data: DoneEventData) => void;
  onError: (data: ErrorEventData) => void;
}

export class OFKMSClient {
  private baseUrl: string;
  private apiKey: string;
  private timeout: number;
  private abortController: AbortController | null = null;

  constructor(apiUrl: string, apiKey: string, timeout = 120) {
    this.baseUrl = apiUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this.timeout = timeout * 1000; // convert to ms
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) {
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    timeoutMs?: number
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      timeoutMs ?? this.timeout
    );

    try {
      const resp = await fetch(url, {
        method,
        headers: this.headers(),
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      if (resp.status === 401) {
        throw new AuthenticationError();
      }
      if (resp.status >= 500) {
        const data = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new ServerError(`Server error: ${data.detail || data.error || resp.statusText}`);
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new APIError(`Request failed (${resp.status}): ${data.detail || data.error || resp.statusText}`);
      }

      return (await resp.json()) as T;
    } catch (err) {
      if (err instanceof APIError) {
        throw err;
      }
      if ((err as Error).name === "AbortError") {
        throw new APIError("Request timed out");
      }
      throw new APIError(
        `Connection failed: ${(err as Error).message}`,
        "Is the OFKMS server running?"
      );
    } finally {
      clearTimeout(timer);
    }
  }

  // ── Public endpoints ──

  async health(): Promise<HealthResponse> {
    return this.request("GET", "/v1/health", undefined, 10000);
  }

  async products(): Promise<ProductsResponse> {
    return this.request("GET", "/v1/products", undefined, 10000);
  }

  async query(
    query: string,
    language?: string,
    product?: string
  ): Promise<QueryResponse> {
    const body: Record<string, unknown> = {
      query,
      include_sources: true,
      include_phases: false,
    };
    if (language) body.language = language;
    if (product) body.product = product;
    return this.request("POST", "/v1/query", body);
  }

  // ── Streaming ──

  async queryStream(
    query: string,
    language: string | undefined,
    product: string | undefined,
    callbacks: StreamCallbacks
  ): Promise<void> {
    this.abortController = new AbortController();
    const url = `${this.baseUrl}/v1/query/stream`;
    const body: Record<string, unknown> = {
      query,
      include_sources: true,
    };
    if (language) body.language = language;
    if (product) body.product = product;

    const timer = setTimeout(
      () => this.abortController?.abort(),
      this.timeout
    );

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify(body),
        signal: this.abortController.signal,
      });

      if (resp.status === 401) {
        throw new AuthenticationError();
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new ServerError(
          `Stream failed (${resp.status}): ${data.detail || data.error || resp.statusText}`
        );
      }

      if (!resp.body) {
        throw new ServerError("No response body for stream");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          if (trimmed.startsWith("event:")) {
            currentEvent = trimmed.slice(6).trim();
          } else if (trimmed.startsWith("data:")) {
            const raw = trimmed.slice(5).trim();
            if (!raw) continue;
            try {
              const data = JSON.parse(raw);
              switch (currentEvent) {
                case "phase":
                  callbacks.onPhase(data as PhaseEventData);
                  break;
                case "answer":
                  callbacks.onAnswer(data as AnswerEventData);
                  break;
                case "done":
                  callbacks.onDone(data as DoneEventData);
                  break;
                case "error":
                  callbacks.onError(data as ErrorEventData);
                  break;
              }
            } catch {
              // skip malformed JSON
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        return; // cancelled by user
      }
      if (err instanceof APIError) throw err;
      throw new APIError(
        `Stream connection failed: ${(err as Error).message}`,
        "Is the OFKMS server running?"
      );
    } finally {
      clearTimeout(timer);
      this.abortController = null;
    }
  }

  cancelStream(): void {
    this.abortController?.abort();
  }
}
