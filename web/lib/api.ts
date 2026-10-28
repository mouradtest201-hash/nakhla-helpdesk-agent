import type { ChatResponse } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// Requests go through the Next.js rewrite to the agent, so the browser never needs CORS.
async function post(path: string, body: unknown): Promise<ChatResponse> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the support assistant. Check that it is running, then try again.", 0);
  }

  const data = await res.json().catch(() => null);
  if (res.status === 429) {
    throw new ApiError("Too many requests in a short time. Wait a moment and try again.", 429);
  }
  if (!res.ok) {
    const reason = data?.reply ?? data?.detail;
    throw new ApiError(typeof reason === "string" ? reason : `The assistant returned an error (${res.status}).`, res.status);
  }
  return data as ChatResponse;
}

export const sendChat = (sessionId: string, message: string, email: string | null) =>
  post("/api/chat", { session_id: sessionId, message, user: email ? { email } : null });

export const decideAction = (sessionId: string, actionId: string, approved: boolean) =>
  post("/api/approve", { session_id: sessionId, action_id: actionId, approved });

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch("/health", { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
