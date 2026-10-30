import type { ChatResponse, Citation, PendingAction, ToolCall, Usage } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCitation(value: unknown): value is Citation {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    (value.url === null || typeof value.url === "string")
  );
}

function isToolCall(value: unknown): value is ToolCall {
  return (
    isRecord(value) &&
    typeof value.tool === "string" &&
    typeof value.ok === "boolean" &&
    typeof value.ms === "number"
  );
}

function isUsage(value: unknown): value is Usage {
  return (
    isRecord(value) &&
    typeof value.prompt_tokens === "number" &&
    typeof value.completion_tokens === "number" &&
    typeof value.cost_usd === "number"
  );
}

function isPendingAction(value: unknown): value is PendingAction {
  return (
    isRecord(value) &&
    typeof value.action_id === "string" &&
    (value.type === "create_ticket" || value.type === "add_ticket_note") &&
    typeof value.summary === "string" &&
    isRecord(value.payload)
  );
}

function isChatResponse(value: unknown): value is ChatResponse {
  return (
    isRecord(value) &&
    typeof value.session_id === "string" &&
    typeof value.reply === "string" &&
    (value.state === null || typeof value.state === "string") &&
    (value.pending_action === null || isPendingAction(value.pending_action)) &&
    (value.result === null || isRecord(value.result)) &&
    Array.isArray(value.citations) &&
    value.citations.every(isCitation) &&
    Array.isArray(value.tool_calls) &&
    value.tool_calls.every(isToolCall) &&
    (value.usage === null || isUsage(value.usage))
  );
}

async function post(path: string, body: unknown): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the support assistant. Check that it is running, then try again.", 0);
  }

  const data: unknown = await response.json().catch(() => null);
  if (response.status === 429) {
    throw new ApiError("Too many requests in a short time. Wait a moment and try again.", 429);
  }
  if (!response.ok) {
    const reason = isRecord(data) ? data.reply ?? data.detail : null;
    throw new ApiError(
      typeof reason === "string" ? reason : `The assistant returned an error (${response.status}).`,
      response.status,
    );
  }
  if (!isChatResponse(data)) {
    throw new ApiError("The assistant returned an invalid response. Please try again.", response.status);
  }
  return data;
}

export const sendChat = (sessionId: string, message: string, email: string | null) =>
  post("/api/chat", { session_id: sessionId, message, user: email ? { email } : null });

export const decideAction = (sessionId: string, actionId: string, approved: boolean) =>
  post("/api/approve", { session_id: sessionId, action_id: actionId, approved });

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}
