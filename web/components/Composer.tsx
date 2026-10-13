"use client";

import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

const MAX_LENGTH = 2000; // matches AskRequest.question in app/api.py

interface Props {
  busy: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
}

/** Question box. Enter sends, Shift+Enter adds a line. */
export function Composer({ busy, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = value.trim().length > 0 && !busy;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!canSend) {
      ref.current?.focus();
      return;
    }
    onSend(value.trim());
    setValue("");
    if (ref.current) ref.current.style.height = "";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  return (
    <form onSubmit={submit} className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-sm shadow-zinc-900/5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
      <label htmlFor="question" className="sr-only">
        Ask a question about F-1 student rules
      </label>
      <textarea
        id="question"
        name="question"
        ref={ref}
        rows={1}
        value={value}
        maxLength={MAX_LENGTH}
        onChange={(e) => {
          setValue(e.target.value);
          autoGrow(e.target);
        }}
        onKeyDown={onKeyDown}
        placeholder="Ask about F-1 student rules…"
        autoComplete="off"
        className="max-h-[200px] min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] leading-6 text-fg placeholder:text-faint focus:outline-none"
      />
      {busy ? (
        <button
          type="button"
          onClick={onStop}
          className="h-10 shrink-0 rounded-xl border border-line px-4 text-sm font-medium text-fg transition-colors hover:bg-surface-2 active:scale-[0.98]"
        >
          Stop
        </button>
      ) : (
        <button
          type="submit"
          className="h-10 shrink-0 rounded-xl bg-accent px-4 text-sm font-semibold text-accent-fg transition-colors hover:bg-accent-hover active:scale-[0.98]"
        >
          Ask
        </button>
      )}
    </form>
  );
}
