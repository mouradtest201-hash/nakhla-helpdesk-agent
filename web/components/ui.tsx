import type { ReactNode } from "react";

export const cn = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(" ");

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "bg-ink/[0.06] text-ink-2",
  accent: "bg-accent/10 text-accent",
  ok: "bg-ok/10 text-ok-fg",
  warn: "bg-warn/15 text-warn-fg",
  danger: "bg-danger/10 text-danger-fg",
};

export function Badge({ tone = "neutral", children, mono }: { tone?: Tone; children: ReactNode; mono?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[4px] px-1.5 py-0.5 text-xs font-medium leading-4",
        mono && "font-mono",
        TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

const PRIORITY: Record<string, Tone> = { P1: "danger", P2: "warn", P3: "neutral", P4: "neutral" };

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge tone={PRIORITY[priority] ?? "neutral"} mono>
      {priority}
    </Badge>
  );
}

const STATE_LABEL: Record<string, { label: string; tone: Tone }> = {
  answered: { label: "Answered", tone: "neutral" },
  clarification: { label: "Needs detail", tone: "warn" },
  refused: { label: "Not permitted", tone: "danger" },
  incident: { label: "Known incident", tone: "warn" },
  awaiting_approval: { label: "Awaiting approval", tone: "accent" },
  completed: { label: "Ticket created", tone: "ok" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
  error: { label: "Error", tone: "danger" },
};

export function StateBadge({ state }: { state?: string | null }) {
  const entry = state ? STATE_LABEL[state] : undefined;
  if (!entry) return null;
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <h2 className="label">{children}</h2>;
}
