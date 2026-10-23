import { useMemo } from "react";
import { citeIndex, splitFollowUps } from "@/lib/answer";
import type { Source } from "@/lib/types";
import { AlertCallout, FollowUps } from "./AnswerExtras";
import { AnswerProse } from "./AnswerProse";
import { SourceCard } from "./SourceCard";

export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      status: "streaming" | "done" | "error";
      text: string;
      sources?: Source[];
      error?: string;
      question: string; // the question this answers, for retry
    };

type AssistantMessage = Extract<ChatMessage, { role: "assistant" }>;

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onAsk: (question: string) => void;
  onRetry: (message: AssistantMessage) => void;
}

function Searching() {
  return (
    <div aria-hidden className="space-y-2.5 pt-1">
      {["w-11/12", "w-full", "w-4/5"].map((w) => (
        <div key={w} className={`h-3.5 ${w} animate-pulse rounded bg-surface-2 motion-reduce:animate-none`} />
      ))}
    </div>
  );
}

function Answer({ message, busy, onAsk, onRetry }: { message: AssistantMessage } & Omit<Props, "messages">) {
  const done = message.status === "done";
  // Citations become links only after `done` brings the verified sources.
  const cites = useMemo(() => citeIndex(done ? message.sources : undefined), [done, message.sources]);
  const { body, followUps } = splitFollowUps(message.text);
  const sources = message.sources ?? [];

  return (
    <article aria-busy={message.status === "streaming"} className="space-y-5">
      <h2 className="flex items-center gap-1.5 text-xs font-medium text-faint">
        <span aria-hidden className="font-serif text-sm text-accent">§</span> Answer
      </h2>

      {/* Warn only about court orders and alerts the answer actually cites, not every retrieved one. */}
      {done && <AlertCallout sources={sources.filter((s) => message.text.includes(s.url))} />}

      {message.status === "error" ? (
        <div className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-100">
          <p>{message.error}</p>
          <button
            type="button"
            onClick={() => onRetry(message)}
            disabled={busy}
            className="mt-2 font-medium underline underline-offset-2 disabled:opacity-50"
          >
            Try Again
          </button>
        </div>
      ) : body ? (
        <AnswerProse
          text={body}
          cites={cites}
          sources={done ? sources : undefined}
          idPrefix={message.id}
          streaming={message.status === "streaming"}
        />
      ) : (
        <Searching />
      )}

      {done && sources.length > 0 && (
        <section aria-label="Sources">
          <h3 className="mb-3 text-xs font-medium text-faint">
            Sources <span className="tabular-nums">· {sources.length}</span>
          </h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {sources.map((s, i) => (
              <SourceCard key={s.chunk_id} source={s} n={i + 1} id={`${message.id}-src-${i + 1}`} />
            ))}
          </div>
        </section>
      )}

      {done && <FollowUps questions={followUps} onAsk={onAsk} disabled={busy} />}
    </article>
  );
}

export function MessageList({ messages, busy, onAsk, onRetry }: Props) {
  return (
    <div className="space-y-10">
      {messages.map((m) =>
        m.role === "user" ? (
          <p
            key={m.id}
            id={m.id}
            className="scroll-mt-40 whitespace-pre-line rounded-xl bg-surface-2 px-4 py-3 text-[15px] font-medium leading-relaxed text-fg [overflow-wrap:anywhere]"
          >
            {m.text}
          </p>
        ) : (
          <Answer key={m.id} message={m} busy={busy} onAsk={onAsk} onRetry={onRetry} />
        ),
      )}
    </div>
  );
}
