"use client";

import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import ReactMarkdown from "react-markdown";
import { FileText, ArrowRight } from "lucide-react";
import type { Citation, Message } from "@/lib/types";
import { StateBadge } from "./ui";

function Reply({ text }: { text: string }) {
  return (
    <div className="reply">
      <ReactMarkdown
        components={{
          a: ({ href, children }) =>
            href && /^https?:\/\//.test(href) ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function Sources({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Sources">
      {citations.map((c) => (
        <li
          key={c.id}
          title={c.title}
          className="flex max-w-full items-center gap-1.5 rounded-[4px] border border-line px-1.5 py-0.5 text-xs text-ink-2"
        >
          <FileText size={12} aria-hidden />
          <span className="font-mono font-medium text-ink">{c.id}</span>
          <span className="truncate">{c.title}</span>
        </li>
      ))}
    </ul>
  );
}

function Entry({ message, onOpenReview }: { message: Message; onOpenReview: () => void }) {
  const isUser = message.role === "user";
  return (
    <motion.li
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-3 sm:grid-cols-[4.5rem_minmax(0,1fr)]"
    >
      <span className="label pt-1">{isUser ? "You" : "Assistant"}</span>
      <div className="min-w-0">
        {isUser ? (
          <p className="inline-block max-w-[68ch] whitespace-pre-wrap rounded-[8px] bg-elevated px-3.5 py-2.5 text-[15px] leading-relaxed">
            {message.text}
          </p>
        ) : (
          <>
            {message.state && message.state !== "answered" && (
              <div className="mb-2">
                <StateBadge state={message.state} />
              </div>
            )}
            {message.text && <Reply text={message.text} />}
            {message.hasDraft && (
              <button
                type="button"
                onClick={onOpenReview}
                className="mt-3 inline-flex items-center gap-1.5 rounded-[6px] border border-accent/30 bg-accent-muted px-2.5 py-1.5 text-sm font-medium text-accent transition-colors hover:border-accent/60 active:translate-y-px lg:pointer-events-none"
              >
                Draft ready for your review
                <ArrowRight size={14} aria-hidden className="lg:hidden" />
                <span className="hidden lg:inline" aria-hidden>
                  →
                </span>
              </button>
            )}
            <Sources citations={message.citations ?? []} />
          </>
        )}
      </div>
    </motion.li>
  );
}

function Thinking() {
  return (
    <li className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-3 sm:grid-cols-[4.5rem_minmax(0,1fr)]" aria-label="Assistant is working">
      <span className="label pt-1">Assistant</span>
      <div className="space-y-2.5 pt-1.5" aria-hidden>
        <div className="skeleton h-3.5 w-[62%]" />
        <div className="skeleton h-3.5 w-[84%]" />
        <div className="skeleton h-3.5 w-[40%]" />
      </div>
    </li>
  );
}

export function MessageList({
  messages,
  loading,
  onOpenReview,
}: {
  messages: Message[];
  loading: boolean;
  onOpenReview: () => void;
}) {
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    end.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "end" });
  }, [messages.length, loading]);

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8">
      <ol role="log" aria-live="polite" aria-label="Conversation" className="mx-auto flex max-w-[56rem] flex-col gap-7">
        {messages.map((m) => (
          <Entry key={m.id} message={m} onOpenReview={onOpenReview} />
        ))}
        {loading && <Thinking />}
      </ol>
      <div ref={end} />
    </div>
  );
}
