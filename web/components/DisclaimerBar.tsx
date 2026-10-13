import { Suspense } from "react";
import { FALLBACK_DISCLAIMER, getMetaForRequest } from "@/lib/meta";

function Notice({ text }: { text: string }) {
  // Split off the first sentence so "not legal advice" reads first.
  const cut = text.indexOf(". ");
  const lead = cut > 0 ? text.slice(0, cut + 1) : text;
  const rest = cut > 0 ? text.slice(cut + 2) : "";
  return (
    <p className="text-[13px] leading-snug text-muted">
      <strong className="font-semibold text-fg">{lead}</strong> {rest}
    </p>
  );
}

async function ApiNotice() {
  const meta = await getMetaForRequest();
  return <Notice text={meta?.disclaimer ?? FALLBACK_DISCLAIMER} />;
}

/** Always visible and not dismissible (CLAUDE.md: Legal guardrails, Privacy). */
export function DisclaimerBar() {
  return (
    <div role="note" aria-label="Legal and privacy notice" className="border-b border-line bg-accent-soft">
      <div className="mx-auto max-w-3xl px-4 py-2.5 sm:px-6">
        <Suspense fallback={<Notice text={FALLBACK_DISCLAIMER} />}>
          <ApiNotice />
        </Suspense>
      </div>
    </div>
  );
}
