"use client";

/**
 * The node views of the Knowledge editor (WS-61 KB-2b, §21.4).
 *
 * Each one draws with the shared primitives (`Button`, `SelectButton`,
 * `<Icon>`), so a block in a page looks like the rest of the app. None of
 * them writes HTML into the page: every value goes through React.
 *
 * ⚠️ `EditorImage` draws each image through `MarkdownImage`. A remote image
 * then loads only on a click (`src/lib/markdownMedia.ts`), as it does in the
 * chat. A page can hold an image that an agent wrote, so the same gate
 * applies. Fence: `src/components/markdownImage.test.ts`.
 */
import { useState } from "react";
import { NodeViewContent, NodeViewWrapper, type NodeViewProps } from "@tiptap/react";

import Icon from "@/components/Icon";
import MarkdownImage from "@/components/MarkdownImage";
import Button from "@/components/ui/Button";
import { SelectButton } from "@/components/ui/SelectButton";

import { CALLOUT_LOOK, languageLabel, languageOptions } from "../../lib/editor/blocks";
import { CALLOUT_TONES, isCalloutTone } from "../../lib/editor/schema";

/** One image of a page. Pure, so a test with no DOM can render it. */
export function EditorImage({ src, alt, title }: { src: string; alt: string; title?: string | null }) {
  return <MarkdownImage src={src} alt={alt} title={title ?? undefined} className="kb-image" />;
}

export function ImageView({ node }: NodeViewProps) {
  return (
    <NodeViewWrapper as="span" className="kb-image-wrap" data-drag-handle="">
      <EditorImage
        src={String(node.attrs.src ?? "")}
        alt={String(node.attrs.alt ?? "")}
        title={node.attrs.title ? String(node.attrs.title) : null}
      />
    </NodeViewWrapper>
  );
}

/** A code block: the language, a copy button, and the code. */
export function CodeBlockView({ node, editor, updateAttributes }: NodeViewProps) {
  const [copied, setCopied] = useState(false);
  const language = (node.attrs.language as string | null) ?? "";
  const copy = () => {
    void navigator.clipboard?.writeText(node.textContent).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <NodeViewWrapper className="kb-code">
      <div className="kb-code-bar" contentEditable={false}>
        {editor.isEditable ? (
          <SelectButton
            label="Code language"
            value={language}
            options={languageOptions(language)}
            onChange={(value) => updateAttributes({ language: value || null })}
            widthClass="w-36"
          />
        ) : (
          <span className="text-[11px] text-muted-foreground">{languageLabel(language) || "Code"}</span>
        )}
        <Button
          variant="ghost"
          size="sm"
          icon={copied ? "Check" : "Copy"}
          onClick={copy}
          aria-label={copied ? "Code copied" : "Copy the code"}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre>
        <NodeViewContent<"code"> as="code" />
      </pre>
    </NodeViewWrapper>
  );
}

/** A callout: the tone's glyph and name, then the blocks inside it. */
export function CalloutView({ node, editor, updateAttributes }: NodeViewProps) {
  const tone = isCalloutTone(node.attrs.tone) ? node.attrs.tone : "note";
  const look = CALLOUT_LOOK[tone];
  return (
    <NodeViewWrapper className="kb-callout" data-callout={tone}>
      <div className="kb-callout-head" contentEditable={false}>
        <Icon name={look.icon} size={14} className="kb-callout-icon shrink-0" />
        {editor.isEditable ? (
          <SelectButton
            label="Callout tone"
            value={tone}
            options={CALLOUT_TONES.map((t) => ({ value: t, label: CALLOUT_LOOK[t].label }))}
            onChange={(value) => updateAttributes({ tone: value })}
            widthClass="w-32"
          />
        ) : (
          <span className="kb-callout-label">{look.label}</span>
        )}
      </div>
      <NodeViewContent className="kb-callout-body" />
    </NodeViewWrapper>
  );
}

/**
 * A toggle: a fold button, the summary, and the body. It opens while a
 * member edits, and it starts folded on the read view. The fold is the
 * reader's own state, so a fold writes nothing to the body.
 */
export function DetailsView({ editor }: NodeViewProps) {
  const [open, setOpen] = useState(editor.isEditable);
  return (
    <NodeViewWrapper className="kb-toggle" data-open={open ? "true" : "false"}>
      <span className="kb-toggle-fold" contentEditable={false}>
        <Button
          variant="ghost"
          size="icon-xs"
          icon={open ? "ChevronDown" : "ChevronRight"}
          aria-expanded={open}
          aria-label={open ? "Fold this section" : "Unfold this section"}
          onClick={() => setOpen((o) => !o)}
        />
      </span>
      <NodeViewContent className="kb-toggle-inner" />
    </NodeViewWrapper>
  );
}
