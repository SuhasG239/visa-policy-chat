import { FLAGGED_DOC_TYPES } from "@/lib/answer";
import type { Source } from "@/lib/types";

/** Warning when an answer cites a court order or agency alert (CLAUDE.md, Web frontend). */
export function AlertCallout({ sources }: { sources: Source[] }) {
  const flagged = sources.filter((s) => FLAGGED_DOC_TYPES.has(s.doc_type));
  if (flagged.length === 0) return null;
  return (
    <div
      role="alert"
      className="rounded-xl border border-orange-300 bg-orange-50 px-4 py-3 text-sm text-orange-950 dark:border-orange-800 dark:bg-orange-950/40 dark:text-orange-100"
    >
      <p className="font-semibold">This answer cites a court order or agency alert.</p>
      <p className="mt-1">
        A policy described here may be suspended, blocked, or changed. Check{" "}
        {flagged.map((s, i) => (
          <span key={s.chunk_id}>
            {i > 0 && ", "}
            <span translate="no" className="font-mono text-[13px]">{s.citation}</span>
          </span>
        ))}{" "}
        and its date before relying on it.
      </p>
    </div>
  );
}

interface FollowUpsProps {
  questions: string[];
  onAsk: (question: string) => void;
  disabled?: boolean;
}

/** The model's suggested next questions, as chips that ask them. */
export function FollowUps({ questions, onAsk, disabled }: FollowUpsProps) {
  if (questions.length === 0) return null;
  return (
    <section aria-label="Suggested follow-up questions">
      <h3 className="mb-2 text-xs font-medium text-faint">Suggested Follow-Ups</h3>
      <ul className="flex flex-wrap gap-2">
        {questions.map((q) => (
          <li key={q}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onAsk(q)}
              className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-sm text-fg transition-colors hover:border-accent hover:text-accent active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {q}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
