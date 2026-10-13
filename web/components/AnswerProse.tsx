import { Fragment, useMemo } from "react";
import { parseMarkdown, type Block, type CiteIndex, type Inline } from "@/lib/markdown";
import type { Source } from "@/lib/types";

interface Props {
  text: string;
  cites: CiteIndex;
  sources?: Source[];
  idPrefix: string; // makes source-card anchors unique per answer
  streaming?: boolean;
}

function Inlines({ nodes, sources, idPrefix }: { nodes: Inline[]; sources?: Source[]; idPrefix: string }) {
  return nodes.map((node, i) => {
    switch (node.t) {
      case "text":
        return <Fragment key={i}>{node.v}</Fragment>;
      case "strong":
        return (
          <strong key={i}>
            <Inlines nodes={node.c} sources={sources} idPrefix={idPrefix} />
          </strong>
        );
      case "em":
        return (
          <em key={i}>
            <Inlines nodes={node.c} sources={sources} idPrefix={idPrefix} />
          </em>
        );
      case "code":
        return <code key={i}>{node.v}</code>;
      case "cite": {
        const citation = sources?.[node.n - 1]?.citation ?? node.label;
        return (
          <sup key={i} className="cite">
            <a href={`#${idPrefix}-src-${node.n}`} aria-label={`Source ${node.n}: ${citation}`} title={citation}>
              {node.n}
            </a>
          </sup>
        );
      }
    }
  });
}

const Caret = () => <span aria-hidden className="caret" />;

function BlockView({ block, sources, idPrefix, caret }: { block: Block; sources?: Source[]; idPrefix: string; caret?: boolean }) {
  switch (block.t) {
    case "p":
      return (
        <p>
          <Inlines nodes={block.c} sources={sources} idPrefix={idPrefix} />
          {caret && <Caret />}
        </p>
      );
    case "h": {
      const Tag = `h${block.level}` as const;
      return (
        <Tag>
          <Inlines nodes={block.c} sources={sources} idPrefix={idPrefix} />
          {caret && <Caret />}
        </Tag>
      );
    }
    case "ul":
    case "ol": {
      const List = block.t;
      return (
        <List>
          {block.items.map((item, i) => (
            <li key={i}>
              <Inlines nodes={item} sources={sources} idPrefix={idPrefix} />
              {caret && i === block.items.length - 1 && <Caret />}
            </li>
          ))}
        </List>
      );
    }
  }
}

/** Renders answer markdown with citations as superscripts linking to source cards. */
export function AnswerProse({ text, cites, sources, idPrefix, streaming }: Props) {
  const blocks = useMemo(() => parseMarkdown(text, cites), [text, cites]);
  return (
    <div className="answer-prose">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} sources={sources} idPrefix={idPrefix} caret={streaming && i === blocks.length - 1} />
      ))}
    </div>
  );
}
