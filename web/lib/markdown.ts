// A deliberately small markdown parser for answer text. It produces plain data
// (no HTML strings), so nothing in an answer can inject markup. Supported:
// paragraphs, #-### headings, - / * / 1. lists, **bold**, *italic*, `code`, and links.
// A link becomes a numbered citation only if its URL belongs to a source;
// any other link renders as plain text (CLAUDE.md: never render a link not in sources).

export type Inline =
  | { t: "text"; v: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "code"; v: string }
  | { t: "cite"; n: number; label: string; keepLabel: boolean };

export type Block =
  | { t: "p"; c: Inline[] }
  | { t: "h"; level: 2 | 3 | 4; c: Inline[] }
  | { t: "ul" | "ol"; items: Inline[][] };

/** Maps a source URL to its 1-based number. Empty while sources are unknown (streaming). */
export type CiteIndex = Map<string, number>;

const URL_PART = String.raw`https?://(?:[^()\s]|\([^()\s]*\))+`;
// "([label](url) (updated 2026-09-30))", the form the system prompt asks for, or a bare link.
const CITE_WRAPPED = new RegExp(String.raw`^\(\[([^\]]+)\]\((${URL_PART})\)(?:\s*\(updated[^)]*\))?\)`);
const CITE_BARE = new RegExp(String.raw`^\[([^\]]+)\]\((${URL_PART})\)(?:\s*\(updated[^)]*\))?`);
// Link text that is itself a legal citation becomes just the superscript number;
// descriptive link text ("[post-completion OPT](...)") stays in the sentence.
const CITATION_LABEL = /^(?:\d+\s+(?:CFR|U\.?S\.?C\.?|FR)\b|§|INA\b|USCIS\b|.*Policy Manual)/i;

export function parseInline(src: string, cites: CiteIndex): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ t: "text", v: text });
    text = "";
  };
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    const link = rest.match(CITE_WRAPPED) ?? rest.match(CITE_BARE);
    if (link) {
      const n = cites.get(link[2]);
      if (n) {
        const keepLabel = !CITATION_LABEL.test(link[1].trim());
        if (keepLabel) text += link[1];
        else text = text.replace(/\s+$/, ""); // superscript hugs the preceding word
        flush();
        out.push({ t: "cite", n, label: link[1], keepLabel });
      } else {
        text += link[1]; // unknown or not-yet-verified link: label only, no anchor
      }
      i += link[0].length;
      continue;
    }
    const strong = rest.match(/^\*\*(.+?)\*\*/);
    if (strong) {
      flush();
      out.push({ t: "strong", c: parseInline(strong[1], cites) });
      i += strong[0].length;
      continue;
    }
    const em = rest.match(/^(?:\*(?!\s)(.+?)\*(?!\*)|_(?!\s)(.+?)_(?!\w))/);
    if (em) {
      flush();
      out.push({ t: "em", c: parseInline(em[1] ?? em[2], cites) });
      i += em[0].length;
      continue;
    }
    const code = rest.match(/^`([^`]+)`/);
    if (code) {
      flush();
      out.push({ t: "code", v: code[1] });
      i += code[0].length;
      continue;
    }
    text += src[i];
    i += 1;
  }
  flush();
  return out;
}

const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

export function parseMarkdown(src: string, cites: CiteIndex): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { t: "ul" | "ol"; items: string[] } | null = null;

  const endPara = () => {
    if (para.length) blocks.push({ t: "p", c: parseInline(para.join(" "), cites) });
    para = [];
  };
  const endList = () => {
    if (list) blocks.push({ t: list.t, items: list.items.map((s) => parseInline(s, cites)) });
    list = null;
  };

  for (const raw of src.split("\n")) {
    const line = raw.trimEnd();
    const heading = line.match(HEADING);
    const bullet = line.match(BULLET);
    const numbered = line.match(NUMBERED);
    if (!line.trim()) {
      endPara();
      endList();
    } else if (heading) {
      endPara();
      endList();
      const level = Math.min(heading[1].length + 1, 4) as 2 | 3 | 4;
      blocks.push({ t: "h", level, c: parseInline(heading[2], cites) });
    } else if (bullet || numbered) {
      endPara();
      const kind = bullet ? "ul" : "ol";
      if (list && list.t !== kind) endList();
      list ??= { t: kind, items: [] };
      list.items.push((bullet ?? numbered)![1]);
    } else if (list && /^\s{2,}/.test(raw)) {
      list.items[list.items.length - 1] += " " + line.trim(); // wrapped list item
    } else {
      endList();
      para.push(line.trim());
    }
  }
  endPara();
  endList();
  return blocks;
}
