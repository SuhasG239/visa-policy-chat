// Typed client for app/api.py. The browser only ever talks to this API;
// nothing here stores questions or answers.
import type { AskRequest, AskResponse, Meta } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message
  }
  return `The server returned an error (${res.status}).`;
}

/** GET /meta. Returns null if the API can't be reached, so the page still renders. */
export async function getMeta(timeoutMs = 3000): Promise<Meta | null> {
  try {
    const res = await fetch(`${API_BASE}/meta`, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok ? ((await res.json()) as Meta) : null;
  } catch {
    return null;
  }
}

/** POST /ask (non-streaming). */
export async function ask(req: AskRequest, signal?: AbortSignal): Promise<AskResponse> {
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  return (await res.json()) as AskResponse;
}

interface StreamHandlers {
  onToken: (text: string) => void;
  signal?: AbortSignal;
}

/**
 * POST /ask/stream. Calls onToken for each `token` event and resolves with the
 * `done` payload, whose `text` has unknown links removed and replaces the streamed text.
 */
export async function askStream(req: AskRequest, { onToken, signal }: StreamHandlers): Promise<AskResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError("Can't reach the answer service. Check that the API is running.");
  }
  if (!res.ok || !res.body) throw new ApiError(await errorMessage(res));

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseEvent(block);
      if (!event) continue;
      if (event.name === "token") onToken((JSON.parse(event.data) as { text: string }).text);
      else if (event.name === "done") return JSON.parse(event.data) as AskResponse;
      else if (event.name === "error") throw new ApiError((JSON.parse(event.data) as { message: string }).message);
    }
  }
  throw new ApiError("The answer stream ended before it finished. Please try again.");
}

function parseEvent(block: string): { name: string; data: string } | null {
  let name = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // keep-alive comment
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return data.length ? { name, data: data.join("\n") } : null;
}
