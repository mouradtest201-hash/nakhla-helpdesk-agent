"use client";

import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, FileText, Lock, ShieldCheck, TriangleAlert, X } from "lucide-react";
import type { Citation, Outcome, PendingAction, ToolCall, Usage } from "@/lib/types";
import { Badge, PriorityBadge, SectionLabel, cn } from "./ui";

const human = (value: unknown) => String(value ?? "").replace(/_/g, " ").replace(/^./, (character) => character.toUpperCase());
const str = (value: unknown) => (value == null ? "" : String(value));

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <dt className="text-ink-2">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

function Frame({ tone, children }: { tone: "draft" | "ok" | "danger" | "muted"; children: ReactNode }) {
  const bar = { draft: "bg-accent", ok: "bg-ok", danger: "bg-danger", muted: "bg-line-strong" }[tone];
  return (
    <div className={cn("overflow-hidden rounded-[4px] border bg-background", tone === "draft" ? "border-accent/30" : "border-line")}>
      <div className={cn("h-[3px]", bar)} aria-hidden />
      {children}
    </div>
  );
}

function DraftSlip({
  action,
  busy,
  failure,
  onDecide,
}: {
  action: PendingAction;
  busy: boolean;
  failure: string | null;
  onDecide: (approved: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const payload = action.payload;
  const isTicket = action.type === "create_ticket";
  const steps = Array.isArray(payload.attempted_steps) ? payload.attempted_steps.map(String) : [];
  const refs = Array.isArray(payload.kb_refs) ? payload.kb_refs.map(String) : [];
  const description = str(isTicket ? payload.description : payload.note);

  return (
    <Frame tone={failure ? "danger" : "draft"}>
      <div className="space-y-4 p-4">
        <div className="space-y-1.5">
          <p className="label flex items-center gap-1.5">
            <Lock size={12} aria-hidden />
            Draft · not submitted
          </p>
          <h3 className="text-[17px] font-semibold leading-snug">
            {isTicket ? str(payload.subject) || action.summary : `Add a note to ${str(payload.ticket_id)}`}
          </h3>
        </div>

        {isTicket && (
          <dl className="divide-y divide-line border-y border-line">
            <Meta label="Priority">
              <PriorityBadge priority={str(payload.priority)} />
            </Meta>
            <Meta label="Category">{human(payload.category)}</Meta>
            <Meta label="Department">{str(payload.department)}</Meta>
            <Meta label="Affected users">
              <span className="font-mono">{str(payload.affected_users ?? 1)}</span>
            </Meta>
            <Meta label="Requester">
              <span className="break-all font-mono text-xs">{str(payload.requester_email)}</span>
            </Meta>
          </dl>
        )}

        {steps.length > 0 && (
          <div className="space-y-1.5">
            <p className="label">Steps already tried</p>
            <ul className="list-disc space-y-1 pl-4 text-sm marker:text-ink-2">
              {steps.map((step, index) => (
                <li key={`${index}-${step}`}>{step}</li>
              ))}
            </ul>
          </div>
        )}

        {description && (
          <div className="space-y-1.5">
            <p className="label">{isTicket ? "Description" : "Note"}</p>
            <p className={cn("whitespace-pre-wrap text-sm", !open && "line-clamp-4")}>{description}</p>
            {description.length > 220 && (
              <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                aria-expanded={open}
                className="text-sm font-medium text-accent hover:text-accent-hover"
              >
                {open ? "Show less" : "Show full text"}
              </button>
            )}
          </div>
        )}

        {refs.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {refs.map((reference) => (
              <Badge key={reference} mono>
                {reference}
              </Badge>
            ))}
          </div>
        )}

        {failure && (
          <p role="alert" className="flex gap-2 rounded-[6px] bg-danger/10 px-3 py-2 text-sm text-danger-fg">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span>{failure} No ticket id was issued. You can retry or reject the draft.</span>
          </p>
        )}
      </div>

      <div className="space-y-2 border-t border-dashed border-line-strong bg-surface p-4">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide(true)}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-[6px] bg-accent text-sm font-medium text-accent-fg transition-colors hover:bg-accent-hover active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Check size={16} aria-hidden />
          {busy ? "Sending approval…" : failure ? "Retry approval" : isTicket ? "Approve and create ticket" : "Approve and add note"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide(false)}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-[6px] border border-line-strong bg-background text-sm font-medium transition-colors hover:bg-elevated active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
        >
          <X size={16} aria-hidden />
          Reject draft
        </button>
        <p className="flex items-center gap-1.5 pt-1 text-xs text-ink-2">
          <ShieldCheck size={13} aria-hidden />
          Nothing reaches the helpdesk until you approve.
        </p>
      </div>
    </Frame>
  );
}

function ResultSlip({ outcome }: { outcome: Exclude<Outcome, { kind: "failed" }> }) {
  if (outcome.kind === "cancelled") {
    return (
      <Frame tone="muted">
        <div className="space-y-1 p-4">
          <p className="label">Draft cancelled</p>
          <p className="text-sm">Nothing was written to the helpdesk. Ask for a different ticket any time.</p>
        </div>
      </Frame>
    );
  }
  const result = outcome.result ?? {};
  const id = str(result.ticket_id);
  return (
    <Frame tone="ok">
      <div className="space-y-4 p-4">
        <div className="space-y-1">
          <p className="label flex items-center gap-1.5 text-ok-fg">
            <Check size={12} aria-hidden />
            {result.note_added ? "Note added" : "Ticket created"}
          </p>
          <p className="font-mono text-2xl font-medium tracking-tight">{id || "—"}</p>
          {str(result.subject) && <p className="text-sm">{str(result.subject)}</p>}
        </div>
        {!result.note_added && (
          <dl className="divide-y divide-line border-y border-line">
            {str(result.priority) && (
              <Meta label="Priority">
                <PriorityBadge priority={str(result.priority)} />
              </Meta>
            )}
            {str(result.category) && <Meta label="Category">{human(result.category)}</Meta>}
            {str(result.assigned_team) && <Meta label="Assigned team">{str(result.assigned_team)}</Meta>}
            {str(result.status) && <Meta label="Status">{human(result.status)}</Meta>}
          </dl>
        )}
        <p className="text-xs text-ink-2">This id was returned by the helpdesk, not generated by the assistant.</p>
      </div>
    </Frame>
  );
}

function EmptySlip() {
  return (
    <div className="space-y-2 rounded-[4px] border border-dashed border-line-strong p-4">
      <p className="label flex items-center gap-1.5">
        <Lock size={12} aria-hidden />
        No draft
      </p>
      <p className="text-sm text-ink-2">
        If your problem needs a ticket, the assistant prepares it here for you to review. Nothing is submitted without
        your approval.
      </p>
    </div>
  );
}

function Activity({ calls }: { calls: ToolCall[] }) {
  return (
    <section className="space-y-2" aria-labelledby="activity-heading">
      <SectionLabel>
        <span id="activity-heading">Helpdesk activity</span>
      </SectionLabel>
      {calls.length === 0 ? (
        <p className="text-sm text-ink-2">No helpdesk lookups for the last answer.</p>
      ) : (
        <ul className="divide-y divide-line rounded-[8px] border border-line">
          {calls.map((call, index) => (
            <li key={`${index}-${call.tool}`} className="flex items-center gap-2.5 px-3 py-2 text-sm">
              <span className={cn("size-1.5 shrink-0 rounded-full", call.ok ? "bg-ok" : "bg-danger")} aria-hidden />
              <span className="font-mono text-[13px]">{call.tool}</span>
              <span className="sr-only">{call.ok ? "succeeded" : "failed"}</span>
              <span className="ml-auto font-mono text-xs text-ink-2">{call.ms} ms</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Evidence({ citations }: { citations: Citation[] }) {
  return (
    <section className="space-y-2" aria-labelledby="evidence-heading">
      <SectionLabel>
        <span id="evidence-heading">Sources</span>
      </SectionLabel>
      {citations.length === 0 ? (
        <p className="text-sm text-ink-2">The last answer didn&apos;t rely on a document.</p>
      ) : (
        <ul className="space-y-1.5">
          {citations.map((citation) => (
            <li key={citation.id} className="flex items-start gap-2 text-sm">
              <FileText size={14} className="mt-1 shrink-0 text-ink-2" aria-hidden />
              <span>
                <span className="font-mono font-medium">{citation.id}</span>
                <span className="text-ink-2"> · {citation.title}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ReviewPanel({
  pending,
  outcome,
  busy,
  citations,
  toolCalls,
  usage,
  onDecide,
}: {
  pending: PendingAction | null;
  outcome: Outcome | null;
  busy: boolean;
  citations: Citation[];
  toolCalls: ToolCall[];
  usage: Usage | null;
  onDecide: (approved: boolean) => void;
}) {
  const failure = outcome?.kind === "failed" ? outcome.message : null;
  const key = pending ? `p-${pending.action_id}` : outcome ? `o-${outcome.kind}-${outcome.actionId}` : "empty";

  return (
    <div className="flex flex-col gap-8 p-5">
      <section className="space-y-3" aria-labelledby="approval-heading">
        <SectionLabel>
          <span id="approval-heading">Ticket approval</span>
        </SectionLabel>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={key}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
          >
            {pending ? (
              <DraftSlip action={pending} busy={busy} failure={failure} onDecide={onDecide} />
            ) : outcome && outcome.kind !== "failed" ? (
              <ResultSlip outcome={outcome} />
            ) : (
              <EmptySlip />
            )}
          </motion.div>
        </AnimatePresence>
      </section>

      <Evidence citations={citations} />
      <Activity calls={toolCalls} />

      {usage && (usage.prompt_tokens > 0 || usage.completion_tokens > 0) && (
        <p className="font-mono text-xs text-ink-2">
          {usage.prompt_tokens + usage.completion_tokens} tokens · ${usage.cost_usd.toFixed(4)}
        </p>
      )}
    </div>
  );
}
