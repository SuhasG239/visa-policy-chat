// Presentation helpers for an answer. These only split and label what the API
// sent; they never change the wording of an answer.
import type { CiteIndex } from "./markdown";
import type { DocType, Source } from "./types";

const FOLLOW_UP_HEADING = /^\s*\**\s*you might also ask:?\s*\**:?\s*$/im;

/**
 * Separates the model's "You might also ask:" list from the answer body.
 * While streaming, everything from the heading on is hidden so it doesn't flash.
 */
export function splitFollowUps(text: string): { body: string; followUps: string[] } {
  const match = FOLLOW_UP_HEADING.exec(text);
  if (!match) return { body: text, followUps: [] };
  const body = text.slice(0, match.index).trimEnd();
  const followUps = text
    .slice(match.index + match[0].length)
    .split("\n")
    .map((l) => l.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)$/)?.[1]?.trim())
    .filter((q): q is string => Boolean(q))
    .map((q) => q.replace(/^\*\*(.*)\*\*$/, "$1"))
    .slice(0, 3);
  return { body, followUps };
}

/** 1-based citation numbers by URL; repeated URLs share the first number. */
export function citeIndex(sources: Source[] | undefined): CiteIndex {
  const index: CiteIndex = new Map();
  sources?.forEach((s, i) => {
    if (!index.has(s.url)) index.set(s.url, i + 1);
  });
  return index;
}

export interface DocTypeStyle {
  label: string;
  badge: string; // Tailwind classes for the badge
  note?: string; // extra warning shown on the card
}

const DOC_TYPES: Record<DocType, DocTypeStyle> = {
  regulation: {
    label: "Regulation",
    badge: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950/60 dark:text-sky-200 dark:ring-sky-800",
  },
  final_rule: {
    label: "Final rule",
    badge: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950/60 dark:text-sky-200 dark:ring-sky-800",
  },
  policy_guidance: {
    label: "Policy guidance",
    badge: "bg-violet-50 text-violet-800 ring-violet-200 dark:bg-violet-950/60 dark:text-violet-200 dark:ring-violet-800",
  },
  proposed_rule: {
    label: "Proposed rule",
    badge: "bg-amber-50 text-amber-900 ring-amber-300 dark:bg-amber-950/60 dark:text-amber-200 dark:ring-amber-700",
    note: "Proposed, not current law.",
  },
  alert: {
    label: "Alert",
    badge: "bg-orange-50 text-orange-900 ring-orange-300 dark:bg-orange-950/60 dark:text-orange-200 dark:ring-orange-700",
  },
  court_order: {
    label: "Court order",
    badge: "bg-rose-50 text-rose-800 ring-rose-300 dark:bg-rose-950/60 dark:text-rose-200 dark:ring-rose-700",
  },
};

export function docTypeStyle(docType: string): DocTypeStyle {
  return (
    DOC_TYPES[docType as DocType] ?? {
      label: docType.replace(/_/g, " "),
      badge: "bg-zinc-100 text-zinc-700 ring-zinc-300 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-zinc-600",
    }
  );
}

export const FLAGGED_DOC_TYPES = new Set(["court_order", "alert"]);

/** True if the date (YYYY-MM-DD) is after today in the viewer's time zone. */
export function isFuture(date: string | null): boolean {
  if (!date) return false;
  const today = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD
  return date > today;
}

export function formatDate(iso: string | null, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }): string {
  if (!iso) return "Unknown";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat("en-US", opts).format(d);
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
