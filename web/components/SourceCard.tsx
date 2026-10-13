import { docTypeStyle, formatDate, hostOf, isFuture } from "@/lib/answer";
import type { Source } from "@/lib/types";

interface Props {
  source: Source;
  n: number;
  id: string;
}

/** One retrieved source: citation, type, scope, dates, and the official link. */
export function SourceCard({ source, n, id }: Props) {
  const style = docTypeStyle(source.doc_type);
  const proposed = source.doc_type === "proposed_rule";
  const future = isFuture(source.effective_date);
  const host = hostOf(source.url);

  return (
    <article
      id={id}
      className={`source-card flex scroll-mt-40 flex-col gap-2.5 rounded-xl border bg-surface p-4 ${
        proposed ? "border-dashed border-amber-400 dark:border-amber-600" : "border-line"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="grid size-5 place-items-center rounded-full bg-surface-2 text-[11px] font-semibold text-muted tabular-nums">
          {n}
        </span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${style.badge}`}>
          {style.label}
        </span>
        {future && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-900 ring-1 ring-inset ring-amber-300 dark:bg-amber-950/60 dark:text-amber-200 dark:ring-amber-700">
            Takes effect {formatDate(source.effective_date)}
          </span>
        )}
      </div>

      <div>
        <p translate="no" className="font-mono text-[13px] font-medium text-fg [overflow-wrap:anywhere]">{source.citation}</p>
        <p className="mt-0.5 text-sm text-muted">{source.title}</p>
      </div>

      {style.note && <p className="text-xs font-medium text-amber-800 dark:text-amber-300">{style.note}</p>}

      {source.scope.length > 0 && (
        <ul aria-label="Topics" className="flex flex-wrap gap-1.5">
          {source.scope.map((tag) => (
            <li key={tag} translate="no" className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">
              {tag}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto flex items-center justify-between gap-3 pt-1 text-xs">
        <span className="text-faint">
          Updated{" "}
          <time dateTime={source.source_updated ?? undefined} className="text-muted">
            {formatDate(source.source_updated)}
          </time>
        </span>
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-accent underline-offset-2 hover:underline"
        >
          {host}
          <span aria-hidden> ↗</span>
          <span className="sr-only"> (opens {source.citation} in a new tab)</span>
        </a>
      </div>
    </article>
  );
}
