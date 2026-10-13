import "server-only";
import { cache } from "react";
import { getMeta } from "./api";

/** /meta, fetched once per request and shared by the header and disclaimer. */
export const getMetaForRequest = cache(() => getMeta());

/** Shown until /meta arrives, and if the API is unreachable. Mirrors chat.DISCLAIMER. */
export const FALLBACK_DISCLAIMER =
  "General legal information from official sources, not legal advice. Not a substitute for an " +
  "immigration attorney, a DOJ-accredited representative, or your school's DSO. Your question " +
  "and the retrieved excerpts are sent to Anthropic to generate an answer.";
