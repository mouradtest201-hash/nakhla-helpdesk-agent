"use client";

import { useEffect, useRef } from "react";
import { ArrowUp } from "lucide-react";
import { cn } from "./ui";

const LIMIT = 4000;

export function Composer({
  value,
  onChange,
  onSend,
  disabled,
  focusSignal,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
  focusSignal: number;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (focusSignal > 0) ref.current?.focus();
  }, [focusSignal]);

  const empty = value.trim().length === 0;
  const near = value.length > LIMIT - 400;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!empty && !disabled) onSend();
      }}
      className="border-t border-line bg-background px-4 py-3 sm:px-8"
    >
      <div className="mx-auto max-w-[56rem]">
        <div className="flex items-end gap-2 rounded-[8px] border border-line-strong bg-background p-2 transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-[var(--ring)]">
          <label htmlFor="message" className="sr-only">
            Describe your problem
          </label>
          <textarea
            id="message"
            ref={ref}
            rows={1}
            maxLength={LIMIT}
            value={value}
            disabled={disabled}
            placeholder="Describe the problem, including any error code"
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                if (!empty && !disabled) onSend();
              }
            }}
            className="max-h-[200px] min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-ink-2 disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={empty || disabled}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-[6px] bg-accent px-3 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-hover active:translate-y-px disabled:cursor-not-allowed disabled:bg-elevated disabled:text-ink-2"
          >
            <ArrowUp size={16} aria-hidden />
            Send message
          </button>
        </div>
        <p className="mt-1.5 flex justify-between text-xs text-ink-2">
          <span>Enter to send · Shift+Enter for a new line</span>
          <span className={cn("font-mono", near && "text-warn-fg")}>
            {value.length}/{LIMIT}
          </span>
        </p>
      </div>
    </form>
  );
}
