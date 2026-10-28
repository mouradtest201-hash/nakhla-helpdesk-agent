"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { MotionConfig } from "motion/react";
import { Moon, RotateCcw, Sun, TriangleAlert } from "lucide-react";
import { ApiError, checkHealth, decideAction, sendChat } from "@/lib/api";
import type { Scenario } from "@/lib/scenarios";
import type { ChatResponse, Citation, Message, Outcome, PendingAction, ToolCall, Usage } from "@/lib/types";
import { Composer } from "./composer";
import { MessageList } from "./message-list";
import { RequesterPanel } from "./requester-panel";
import { ReviewPanel } from "./review-panel";
import { cn } from "./ui";

const DRAFT_MARKER = "**Ticket draft - not created yet.**";
const uid = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;

type Tab = "chat" | "review" | "requester";

function toMessage(res: ChatResponse): Message {
  const idx = res.reply.indexOf(DRAFT_MARKER);
  const hasMarker = idx >= 0;
  return {
    id: uid(),
    role: "assistant",
    text: hasMarker ? res.reply.slice(0, idx).trim() : res.reply,
    state: res.state,
    citations: res.citations,
    hasDraft: !!res.pending_action && res.state === "awaiting_approval",
  };
}

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}
const getDark = () => document.documentElement.classList.contains("dark");
const getDarkServer = () => false;

export function Portal() {
  const [sessionId, setSessionId] = useState("");
  const [email, setEmail] = useState<string | null>("sara.mikhail@nakhla.example");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; retryText?: string } | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [online, setOnline] = useState<boolean | null>(null);
  const dark = useSyncExternalStore(subscribeTheme, getDark, getDarkServer);
  const [focusSignal, setFocusSignal] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    let alive = true;
    const ping = async () => {
      const ok = await checkHealth();
      if (alive) setOnline(ok);
    };
    ping();
    const timer = setInterval(ping, 20000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {}
  };

  const absorb = (res: ChatResponse) => {
    setCitations(res.citations ?? []);
    setToolCalls(res.tool_calls ?? []);
    setUsage(res.usage ?? null);
  };

  const send = useCallback(
    async (text: string) => {
      if (inFlight.current || !text.trim()) return;
      inFlight.current = true;
      setError(null);
      setLoading(true);
      setMessages((m) => [...m, { id: uid(), role: "user", text }]);
      // Sessions are created on first use, so no random id is generated during prerender
      const session = sessionId || uid();
      if (!sessionId) setSessionId(session);
      try {
        const res = await sendChat(session, text, email);
        setMessages((m) => [...m, toMessage(res)]);
        absorb(res);
        // A reply that isn't about the draft doesn't clear it: the server still holds it.
        if (res.pending_action) {
          setPending(res.pending_action);
          setOutcome(null);
        }
      } catch (e) {
        const message = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
        setError({ text: message, retryText: text });
      } finally {
        inFlight.current = false;
        setLoading(false);
      }
    },
    [sessionId, email],
  );

  const submit = () => {
    const text = draft.trim();
    setDraft("");
    void send(text);
  };

  const decide = async (approved: boolean) => {
    if (!pending || inFlight.current) return;
    inFlight.current = true;
    setError(null);
    setBusy(true);
    const actionId = pending.action_id;
    try {
      const res = await decideAction(sessionId, actionId, approved);
      setMessages((m) => [...m, toMessage(res)]);
      absorb(res);
      if (res.state === "completed") {
        setPending(null);
        setOutcome({ kind: "completed", result: res.result, actionId });
      } else if (res.state === "cancelled") {
        setPending(null);
        setOutcome({ kind: "cancelled", actionId });
      } else if (res.state === "failed") {
        setPending(res.pending_action ?? pending);
        setOutcome({ kind: "failed", message: "The helpdesk couldn't complete this request.", actionId });
      } else if (res.pending_action) {
        setPending(res.pending_action);
      }
    } catch (e) {
      setError({ text: e instanceof ApiError ? e.message : "Something went wrong. Please try again." });
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const reset = () => {
    setSessionId("");
    setMessages([]);
    setDraft("");
    setError(null);
    setPending(null);
    setOutcome(null);
    setCitations([]);
    setToolCalls([]);
    setUsage(null);
    setTab("chat");
    setFocusSignal((n) => n + 1);
  };

  const useScenario = (s: Scenario) => {
    setEmail(s.email);
    setDraft(s.message);
    setTab("chat");
    setFocusSignal((n) => n + 1);
  };

  const tabs: { id: Tab; label: string; lgHidden?: boolean }[] = [
    { id: "chat", label: "Conversation" },
    { id: "review", label: "Review", lgHidden: true },
    { id: "requester", label: "Requester" },
  ];

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex h-dvh flex-col">
        <a
          href="#message"
          className="sr-only z-50 rounded-[6px] bg-ink px-3 py-2 text-background focus:not-sr-only focus:absolute focus:left-3 focus:top-3"
        >
          Skip to message box
        </a>

        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line px-4 sm:px-5">
          <span
            className="grid size-7 place-items-center rounded-[6px] bg-ink text-sm font-semibold text-background"
            aria-hidden
          >
            N
          </span>
          <div className="min-w-0 leading-tight">
            <h1 className="truncate text-[15px] font-semibold">Nakhla IT Support</h1>
            <p className="hidden truncate font-mono text-[11px] text-ink-2 sm:block">
              {sessionId ? `session ${sessionId.slice(0, 8)}` : "no session yet"}
            </p>
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            <span className="mr-1 hidden items-center gap-1.5 text-xs text-ink-2 sm:flex" role="status">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  online === null ? "bg-line-strong" : online ? "bg-ok" : "bg-danger",
                )}
                aria-hidden
              />
              {online === null ? "Checking assistant" : online ? "Assistant online" : "Assistant offline"}
            </span>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
              className="grid size-9 place-items-center rounded-[6px] text-ink-2 transition-colors hover:bg-elevated hover:text-ink active:translate-y-px"
            >
              {dark ? <Sun size={18} aria-hidden /> : <Moon size={18} aria-hidden />}
            </button>
            <button
              type="button"
              onClick={reset}
              className="inline-flex h-9 items-center gap-1.5 rounded-[6px] border border-line-strong px-3 text-sm font-medium transition-colors hover:bg-elevated active:translate-y-px"
            >
              <RotateCcw size={15} aria-hidden />
              New conversation
            </button>
          </div>
        </header>

        <nav aria-label="Sections" className="flex shrink-0 gap-1 border-b border-line px-3 xl:hidden">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={tab === t.id ? "page" : undefined}
              onClick={() => setTab(t.id)}
              className={cn(
                "relative px-3 py-2.5 text-sm font-medium transition-colors",
                t.lgHidden && "lg:hidden",
                tab === t.id ? "text-ink" : "text-ink-2 hover:text-ink",
              )}
            >
              {t.label}
              {t.id === "review" && pending && (
                <span className="absolute right-1 top-2 size-1.5 rounded-full bg-accent" aria-label="Draft waiting" />
              )}
              {tab === t.id && <span className="absolute inset-x-3 bottom-0 h-0.5 bg-accent" aria-hidden />}
            </button>
          ))}
        </nav>

        <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[17rem_minmax(0,1fr)_22rem]">
          <aside
            aria-label="Requester"
            className={cn(
              "scroll-thin min-h-0 overflow-y-auto border-line bg-surface xl:block xl:border-r",
              tab === "requester" ? "block" : "hidden",
            )}
          >
            <RequesterPanel email={email} onEmail={setEmail} onScenario={useScenario} disabled={loading || busy} />
          </aside>

          <section
            aria-label="Conversation"
            className={cn(
              "min-h-0 min-w-0 flex-col",
              tab === "chat" ? "flex" : tab === "review" ? "hidden lg:flex" : "hidden xl:flex",
            )}
          >
            {messages.length === 0 && !loading ? (
              <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-10 sm:px-8">
                <div className="mx-auto max-w-[56rem]">
                  <p className="label mb-3">New conversation</p>
                  <h2 className="max-w-[18ch] text-[31px] font-semibold leading-[1.1] tracking-tight sm:text-[39px]">
                    What can we help you with?
                  </h2>
                  <p className="mt-4 max-w-[52ch] text-ink-2">
                    Describe the problem and include any error code you see. Answers come from the current knowledge
                    base, with sources. If a ticket is needed, you review it before anything is sent.
                    <span className="hidden xl:inline"> Pick a scenario on the left to see how it behaves.</span>
                  </p>
                </div>
              </div>
            ) : (
              <MessageList messages={messages} loading={loading} onOpenReview={() => setTab("review")} />
            )}

            {error && (
              <div role="alert" className="border-t border-danger/30 bg-danger/10 px-4 py-2.5 sm:px-8">
                <div className="mx-auto flex max-w-[56rem] items-start gap-2 text-sm text-danger-fg">
                  <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden />
                  <span className="flex-1">{error.text}</span>
                  {error.retryText && (
                    <button
                      type="button"
                      onClick={() => void send(error.retryText!)}
                      className="font-medium underline underline-offset-2"
                    >
                      Try again
                    </button>
                  )}
                </div>
              </div>
            )}

            <Composer value={draft} onChange={setDraft} onSend={submit} disabled={loading || busy} focusSignal={focusSignal} />
          </section>

          <aside
            aria-label="Review"
            className={cn(
              "scroll-thin min-h-0 overflow-y-auto border-line bg-surface lg:border-l",
              tab === "review" ? "block" : tab === "chat" ? "hidden lg:block" : "hidden xl:block",
            )}
          >
            <ReviewPanel
              pending={pending}
              outcome={outcome}
              busy={busy}
              citations={citations}
              toolCalls={toolCalls}
              usage={usage}
              onDecide={decide}
            />
          </aside>
        </main>
      </div>
    </MotionConfig>
  );
}
