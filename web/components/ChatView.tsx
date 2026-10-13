"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, askStream } from "@/lib/api";
import type { HistoryMessage } from "@/lib/types";
import { Composer } from "./Composer";
import { MessageList, type ChatMessage } from "./MessageList";

const EXAMPLES = [
  "Can I work on campus, and how many hours?",
  "How long is the grace period after OPT ends?",
  "How many days of unemployment are allowed on STEM OPT?",
  "Can I travel outside the U.S. while on OPT?",
];
const MAX_HISTORY_MESSAGES = 6; // the backend keeps the last 3 turns

let nextId = 0;
const newId = (prefix: string) => `${prefix}-${++nextId}`;

/**
 * The conversation. State lives only in memory: nothing is written to
 * localStorage, cookies, or analytics (CLAUDE.md, Guardrails for the frontend).
 */
export function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const lastQuestionId = useRef<string | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Bring each new question into view once; don't chase the stream token by token.
  useEffect(() => {
    if (lastQuestionId.current) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      document.getElementById(lastQuestionId.current)?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      lastQuestionId.current = null;
    }
  }, [messages.length]);

  const updateAnswer = (id: string, patch: (m: Extract<ChatMessage, { role: "assistant" }>) => Partial<ChatMessage>) =>
    setMessages((prev) =>
      prev.map((m) => (m.id === id && m.role === "assistant" ? ({ ...m, ...patch(m) } as ChatMessage) : m)),
    );

  const send = useCallback(
    async (question: string, base: ChatMessage[]) => {
      // Only complete question/answer pairs go back as history.
      const pairs: HistoryMessage[] = [];
      base.forEach((m, i) => {
        const next = base[i + 1];
        if (m.role === "user" && next?.role === "assistant" && next.status === "done") {
          pairs.push({ role: "user", content: m.text }, { role: "assistant", content: next.text });
        }
      });
      const history = pairs.slice(-MAX_HISTORY_MESSAGES);

      const userMsg: ChatMessage = { id: newId("q"), role: "user", text: question };
      const answerId = newId("a");
      lastQuestionId.current = userMsg.id;
      setMessages([...base, userMsg, { id: answerId, role: "assistant", status: "streaming", text: "", question }]);
      setBusy(true);
      setAnnouncement("Searching official sources…");

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const result = await askStream(
          { question, history },
          { signal: controller.signal, onToken: (t) => updateAnswer(answerId, (m) => ({ text: m.text + t })) },
        );
        updateAnswer(answerId, () => ({ status: "done", text: result.text, sources: result.sources }));
        setAnnouncement(`Answer ready with ${result.sources.length} sources.`);
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          updateAnswer(answerId, () => ({ status: "error", error: "You stopped this answer before it finished. Select Try Again to ask it again." }));
          setAnnouncement("Answer stopped.");
        } else {
          const msg = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
          updateAnswer(answerId, () => ({ status: "error", error: msg }));
          setAnnouncement(msg);
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [],
  );

  const ask = useCallback((q: string) => void send(q, messages), [send, messages]);
  const retry = useCallback(
    (answer: Extract<ChatMessage, { role: "assistant" }>) => {
      const i = messages.findIndex((m) => m.id === answer.id);
      void send(answer.question, messages.slice(0, Math.max(0, i - 1)));
    },
    [send, messages],
  );

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 sm:px-6">
      <div role="log" aria-label="Conversation" className="flex-1 py-8">
        {messages.length === 0 ? (
          <div className="space-y-6 pt-4 sm:pt-10">
            <div className="space-y-2">
              <h2 className="font-serif text-2xl font-semibold tracking-tight text-fg text-balance sm:text-3xl">
                Ask About F-1 Student Rules
              </h2>
              <p className="max-w-[60ch] text-[15px] leading-relaxed text-muted">
                Answers come only from the regulations in the knowledge base, with a numbered citation for each fact.
              </p>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2" aria-label="Example questions">
              {EXAMPLES.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    onClick={() => ask(q)}
                    className="w-full rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm text-fg transition-colors hover:border-accent active:scale-[0.99]"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <MessageList messages={messages} busy={busy} onAsk={ask} onRetry={retry} />
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div className="sticky bottom-0 bg-gradient-to-t from-bg from-70% to-transparent pb-[max(1rem,env(safe-area-inset-bottom))] pt-6">
        <Composer busy={busy} onSend={ask} onStop={() => abortRef.current?.abort()} />
      </div>
    </div>
  );
}
