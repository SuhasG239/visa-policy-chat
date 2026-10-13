import { Suspense } from "react";
import { formatDate } from "@/lib/answer";
import { getMetaForRequest } from "@/lib/meta";

async function MetaStatus() {
  const meta = await getMetaForRequest();
  if (!meta) {
    return <span className="text-xs text-faint">Answer service offline</span>;
  }
  return (
    <>
      <span className="hidden text-xs text-faint sm:inline">
        Sources synced{" "}
        <time dateTime={meta.last_synced ?? undefined} className="text-muted">
          {formatDate(meta.last_synced)}
        </time>
      </span>
      <span
        translate="no"
        className="rounded-md border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-muted"
        title="Model that writes the answers"
      >
        {meta.model}
      </span>
    </>
  );
}

function MetaSkeleton() {
  return <span aria-hidden className="h-5 w-40 animate-pulse rounded-md bg-surface-2 motion-reduce:animate-none" />;
}

export function SiteHeader() {
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden
            className="grid size-8 shrink-0 place-items-center rounded-md bg-accent font-serif text-lg font-semibold text-accent-fg"
          >
            §
          </span>
          <div className="min-w-0">
            <h1 translate="no" className="text-[15px] font-semibold tracking-tight">Visa Policy Chat</h1>
            <p className="truncate text-xs text-faint">F-1 student rules, grounded in official sources</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Suspense fallback={<MetaSkeleton />}>
            <MetaStatus />
          </Suspense>
        </div>
      </div>
    </header>
  );
}
