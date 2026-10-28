export type Citation = { id: string; title: string; url: string | null };
export type ToolCall = { tool: string; ok: boolean; ms: number };
export type Usage = { prompt_tokens: number; completion_tokens: number; cost_usd: number };

export type PendingAction = {
  action_id: string;
  type: "create_ticket" | "add_ticket_note";
  summary: string;
  payload: Record<string, unknown>;
};

export type ChatResponse = {
  session_id: string;
  reply: string;
  state: string | null;
  pending_action: PendingAction | null;
  result: Record<string, unknown> | null;
  citations: Citation[];
  tool_calls: ToolCall[];
  usage: Usage | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  state?: string | null;
  citations?: Citation[];
  hasDraft?: boolean;
};

export type Outcome =
  | { kind: "completed"; result: Record<string, unknown> | null; actionId: string }
  | { kind: "cancelled"; actionId: string }
  | { kind: "failed"; message: string; actionId: string };
