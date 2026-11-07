"use client";

import Icon from "@/components/Icon";
import { useCallback, useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { uploadSignatureImage } from "../lib/api";
import {
  EditorContent, ReactNodeViewRenderer, NodeViewWrapper, useEditor,
  useEditorState, type Editor, type NodeViewProps,
} from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import TiptapImage from "@tiptap/extension-image";
import { TableKit } from "@tiptap/extension-table";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { Placeholder } from "@tiptap/extensions";

/**
 * Rich email-signature editor — Tiptap (MIT, ProseMirror) with a
 * signature-focused toolbar: text formatting, font size + colour, alignment,
 * lists, links, images (by URL, or uploaded to a hosted https address) that
 * can be DRAGGED to reposition and RESIZED by their corner handle, resizable
 * tables with row/column controls, and a raw-HTML source view for full
 * control. The two views share one HTML string, so switching round-trips
 * losslessly. Output is DOMPurify-sanitised HTML that mail clients render
 * (tables + inline styles).
 *
 * WS-17 SG-1 (D-EM-67, D-EM-70): an uploaded image goes to the server, which
 * re-encodes and hosts it, and the editor inserts its https address. The
 * editor makes no `data:` URI. The server cleans the HTML again on save with
 * the same tags, attributes and URL schemes (`routes/email/signature.py`).
 */

// Signatures are user-authored but we still sanitise: strip scripts/handlers and
// keep only presentational markup + links + images (the tags a mail client will
// actually render). The server allowlist (`clean_signature_html`) holds the
// same tags, attributes and schemes, so the editor shows what the save keeps.
const SIG_ALLOWED_TAGS = [
  "a", "b", "strong", "i", "em", "u", "s", "strike", "span", "div", "p", "br",
  "hr", "img", "ul", "ol", "li",
  "table", "tbody", "thead", "tr", "td", "th", "colgroup", "col",
  "h1", "h2", "h3", "h4", "font", "small",
];
const SIG_ALLOWED_ATTR = [
  "href", "target", "rel", "src", "alt", "width", "height", "style",
  "color", "align", "border", "cellpadding", "cellspacing", "class",
  "colspan", "rowspan",
];
/** The URL schemes of a kept `href` or `src`. The server's `_clean_url`
 *  keeps the same three. */
export const SIG_ALLOWED_URI = /^(?:https:|mailto:|tel:)/i;

export function cleanSignatureHtml(html: string): string {
  if (typeof window === "undefined") return html || "";
  return DOMPurify.sanitize(html || "", {
    ALLOWED_TAGS: SIG_ALLOWED_TAGS,
    ALLOWED_ATTR: SIG_ALLOWED_ATTR,
    // https, mailto and tel only, for links and images alike (D-EM-70). Gmail
    // draws no `data:` image, and a plain `http:` image is a tracking risk.
    ALLOWED_URI_REGEXP: SIG_ALLOWED_URI,
    ADD_ATTR: ["target"],
  });
}

// ── Resizable, draggable image node ───────────────────────────────────────────

/** Image node view: click to select, drag the body to move it anywhere in the
 *  signature, drag the corner handle to resize (stored as a width attribute,
 *  which email clients honour). */
function ResizableImageView({ node, updateAttributes, selected }: NodeViewProps) {
  const imgRef = useRef<HTMLImageElement>(null);

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = imgRef.current?.offsetWidth ?? 120;
    const move = (ev: PointerEvent) => {
      const w = Math.max(24, Math.round(startW + (ev.clientX - startX)));
      updateAttributes({ width: String(w) });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <NodeViewWrapper
      as="span"
      className={`relative inline-block leading-none align-middle ${
        selected ? "ring-2 ring-primary rounded-sm" : ""
      }`}
      data-drag-handle
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={node.attrs.src}
        alt={node.attrs.alt ?? ""}
        width={node.attrs.width ?? undefined}
        className="inline-block max-w-full h-auto align-middle"
        draggable={false}
      />
      {selected && (
        <span
          onPointerDown={startResize}
          title="Drag to resize"
          className="absolute -bottom-1 -right-1 h-3 w-3 cursor-se-resize rounded-sm border border-background bg-primary"
        />
      )}
    </NodeViewWrapper>
  );
}

/** Tiptap Image extended with a width attribute (the resize handle writes it;
 *  <img width> is the one sizing every mail client honours) and the resizable
 *  node view. Inline, so a logo can sit beside text.
 *
 *  WS-17 SG-1: a new upload is a hosted https image, and the editor makes no
 *  `data:` URI. `allowBase64` stays TRUE for a LEGACY `data:` image only. With
 *  false, Tiptap drops that image at load, and an edit then saves the
 *  signature without the member's logo. The save hosts each `data:` image of
 *  a changed signature (`signature_for_save`), or refuses with a plain
 *  sentence, so the image is converted and never lost. DOMPurify keeps a
 *  `data:` `src` on `img` by its default `DATA_URI_TAGS`. */
export const SigImage = TiptapImage.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("width"),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.width ? { width: attrs.width } : {},
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(ResizableImageView);
  },
}).configure({ inline: true, allowBase64: true });

// ── Toolbar chrome ────────────────────────────────────────────────────────────

const TB_BTN =
  "p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-secondary " +
  "transition-colors";
const TB_ON = "p-1.5 rounded bg-primary/15 text-primary transition-colors";

function TBtn({
  title, active, disabled, onClick, children,
}: {
  title: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      className={`${active ? TB_ON : TB_BTN} disabled:opacity-40`}
      onMouseDown={(e) => e.preventDefault()} // keep the editor selection
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span className="mx-1 h-4 w-px bg-border" />;
}

const FONT_SIZES = ["12px", "13px", "14px", "16px", "18px", "22px"];

/** The sentence of a server with no https address to host an image
 *  (§23.3.9). The server's 503 says the same words. */
export const UPLOAD_OFF = "Image upload is not set up on this server yet. Ask your admin.";

// ── The editor ────────────────────────────────────────────────────────────────

export function SignatureEditor({
  value,
  onChange,
  accountId,
  uploadAvailable = true,
  upload = uploadSignatureImage,
}: {
  value: string;
  onChange: (html: string) => void;
  /** The mailbox whose signature this is. The upload needs it, because only
   *  a member who may send from the mailbox may upload. */
  accountId?: string;
  /** False when the server cannot host an image (`signature_image_upload`).
   *  The upload button is then off, with {@link UPLOAD_OFF}. */
  uploadAvailable?: boolean;
  /** The upload call. A test passes a fake. */
  upload?: typeof uploadSignatureImage;
}) {
  // The HTML we last emitted — external `value` changes that didn't originate
  // here (async settings load, reset) are pushed INTO the editor; our own
  // keystrokes are not, so the caret never jumps.
  const lastEmitted = useRef<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const editor = useEditor({
    immediatelyRender: false, // SSR-safe under Next
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        code: false,
        codeBlock: false,
        link: { openOnClick: false, autolink: true, defaultProtocol: "https" },
      }),
      TextStyleKit, // colour + font size/family riding on <span style>
      TextAlign.configure({ types: ["paragraph"] }),
      TableKit.configure({ table: { resizable: true } }),
      SigImage,
      Placeholder.configure({
        placeholder:
          "Type your signature — format it, add tables, links or images…",
      }),
    ],
    content: cleanSignatureHtml(value),
    onUpdate: ({ editor: ed }) => {
      const html = ed.isEmpty ? "" : ed.getHTML();
      lastEmitted.current = html;
      onChange(html);
    },
  });

  // Adopt external value changes (initial async load, HTML-mode edits).
  useEffect(() => {
    if (!editor || value === lastEmitted.current) return;
    lastEmitted.current = value;
    editor.commands.setContent(cleanSignatureHtml(value));
  }, [value, editor]);

  // Subscribe to the states the toolbar reflects (re-renders only on change).
  const s = useEditorState({
    editor,
    selector: ({ editor: ed }: { editor: Editor | null }) =>
      ed
        ? {
            bold: ed.isActive("bold"),
            italic: ed.isActive("italic"),
            underline: ed.isActive("underline"),
            strike: ed.isActive("strike"),
            bullet: ed.isActive("bulletList"),
            ordered: ed.isActive("orderedList"),
            link: ed.isActive("link"),
            table: ed.isActive("table"),
            alignLeft: ed.isActive({ textAlign: "left" }),
            alignCenter: ed.isActive({ textAlign: "center" }),
            alignRight: ed.isActive({ textAlign: "right" }),
            fontSize: (ed.getAttributes("textStyle").fontSize as string) || "",
            color: (ed.getAttributes("textStyle").color as string) || "",
            canUndo: ed.can().undo(),
            canRedo: ed.can().redo(),
          }
        : null,
  });

  const addLink = useCallback(() => {
    if (!editor) return;
    const prev = (editor.getAttributes("link").href as string) || "";
    const url = window.prompt("Link URL (https://…)", prev);
    if (url === null) return;
    if (!url.trim()) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link")
      .setLink({ href: url.trim() }).run();
  }, [editor]);

  const addImageByUrl = useCallback(() => {
    if (!editor) return;
    const url = window.prompt("Image URL (https://…)");
    if (url?.trim()) {
      editor.chain().focus().setImage({ src: url.trim() }).run();
    }
  }, [editor]);

  // The server re-encodes the file and hosts it. The editor inserts the https
  // address it answers, never the file itself (WS-17 SG-1, D-EM-67).
  const onPickImage = useCallback(
    async (file: File | undefined) => {
      if (!file || !editor || !accountId) return;
      setUploading(true);
      setUploadError(null);
      try {
        const image = await upload(accountId, file);
        editor.chain().focus().setImage({ src: image.url, alt: "" }).run();
      } catch (e) {
        setUploadError((e as Error).message || "The upload failed. Try again.");
      } finally {
        setUploading(false);
      }
    },
    [editor, accountId, upload],
  );

  if (!editor) {
    // First client render (immediatelyRender: false) — keep the frame stable.
    return (
      <div className="rounded-lg border border-border bg-background min-h-[200px]" />
    );
  }
  const chain = () => editor.chain().focus();

  return (
    <div className="rounded-lg border border-border overflow-hidden bg-background">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-0.5 px-2 py-1.5 border-b border-border bg-secondary/40">
        <TBtn title="Undo" disabled={!s?.canUndo} onClick={() => chain().undo().run()}>
          <Icon name="Undo2" size={14} />
        </TBtn>
        <TBtn title="Redo" disabled={!s?.canRedo} onClick={() => chain().redo().run()}>
          <Icon name="Redo2" size={14} />
        </TBtn>
        <Divider />
        <TBtn title="Bold" active={s?.bold} onClick={() => chain().toggleBold().run()}>
          <Icon name="Bold" size={14} />
        </TBtn>
        <TBtn title="Italic" active={s?.italic} onClick={() => chain().toggleItalic().run()}>
          <Icon name="Italic" size={14} />
        </TBtn>
        <TBtn title="Underline" active={s?.underline} onClick={() => chain().toggleUnderline().run()}>
          <Icon name="Underline" size={14} />
        </TBtn>
        <TBtn title="Strikethrough" active={s?.strike} onClick={() => chain().toggleStrike().run()}>
          <Icon name="Strikethrough" size={14} />
        </TBtn>
        <Divider />
        {/* Font size + colour ride on <span style> — email-client-safe. */}
        <select
          title="Font size"
          aria-label="Font size"
          value={s?.fontSize || ""}
          onMouseDown={(e) => e.stopPropagation()}
          onChange={(e) => {
            const v = e.target.value;
            if (v) chain().setFontSize(v).run();
            else chain().unsetFontSize().run();
          }}
          className="h-6 rounded border border-border bg-background px-1 text-[11px] text-foreground outline-none"
        >
          <option value="">Size</option>
          {FONT_SIZES.map((f) => (
            <option key={f} value={f}>{f.replace("px", "")}</option>
          ))}
        </select>
        <label
          title="Text colour"
          className={`${TB_BTN} cursor-pointer flex items-center`}
          onMouseDown={(e) => e.preventDefault()}
        >
          <span
            className="block h-3.5 w-3.5 rounded-sm border border-border"
            style={{ backgroundColor: s?.color || "currentColor" }}
          />
          <input
            type="color"
            className="sr-only"
            value={s?.color || "#e6eaf0"}
            onChange={(e) => chain().setColor(e.target.value).run()}
          />
        </label>
        <Divider />
        <TBtn title="Align left" active={s?.alignLeft} onClick={() => chain().setTextAlign("left").run()}>
          <Icon name="AlignLeft" size={14} />
        </TBtn>
        <TBtn title="Align centre" active={s?.alignCenter} onClick={() => chain().setTextAlign("center").run()}>
          <Icon name="AlignCenter" size={14} />
        </TBtn>
        <TBtn title="Align right" active={s?.alignRight} onClick={() => chain().setTextAlign("right").run()}>
          <Icon name="AlignRight" size={14} />
        </TBtn>
        <Divider />
        <TBtn title="Bulleted list" active={s?.bullet} onClick={() => chain().toggleBulletList().run()}>
          <Icon name="List" size={14} />
        </TBtn>
        <TBtn title="Numbered list" active={s?.ordered} onClick={() => chain().toggleOrderedList().run()}>
          <Icon name="ListOrdered" size={14} />
        </TBtn>
        <Divider />
        <TBtn title={s?.link ? "Edit link" : "Link selected text"} active={s?.link} onClick={addLink}>
          <Icon name="Link2" size={14} />
        </TBtn>
        {s?.link && (
          <TBtn title="Remove link" onClick={() => chain().extendMarkRange("link").unsetLink().run()}>
            <Icon name="Link2Off" size={14} />
          </TBtn>
        )}
        <TBtn title="Insert image by URL" onClick={addImageByUrl}>
          <Icon name="Image" size={14} />
        </TBtn>
        <TBtn
          title={!uploadAvailable ? UPLOAD_OFF : uploading ? "Uploading the image…" : "Upload an image"}
          disabled={!uploadAvailable || uploading || !accountId}
          onClick={() => fileRef.current?.click()}
        >
          <Icon name="Upload" size={14} />
        </TBtn>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/gif"
          className="hidden"
          aria-label="Upload an image"
          onChange={(e) => { void onPickImage(e.target.files?.[0]); e.target.value = ""; }}
        />
        <TBtn
          title="Insert table (3×3)"
          active={s?.table}
          onClick={() =>
            chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        >
          <Icon name="Table" size={14} />
        </TBtn>
        <TBtn title="Horizontal rule" onClick={() => chain().setHorizontalRule().run()}>
          <Icon name="Minus" size={14} />
        </TBtn>
        <TBtn title="Clear formatting" onClick={() => chain().unsetAllMarks().unsetTextAlign().run()}>
          <Icon name="RemoveFormatting" size={14} />
        </TBtn>
      </div>

      {/* Table controls — appear only while the caret is inside a table. */}
      {s?.table && (
        <div className="flex flex-wrap items-center gap-1 px-2 py-1 border-b border-border bg-primary/5 text-[10px]">
          <span className="text-muted-foreground mr-0.5">Table:</span>
          {([
            ["+ Row above", () => chain().addRowBefore().run()],
            ["+ Row below", () => chain().addRowAfter().run()],
            ["+ Col left", () => chain().addColumnBefore().run()],
            ["+ Col right", () => chain().addColumnAfter().run()],
            ["− Row", () => chain().deleteRow().run()],
            ["− Col", () => chain().deleteColumn().run()],
            ["Header row", () => chain().toggleHeaderRow().run()],
            ["Merge/split", () => chain().mergeOrSplit().run()],
          ] as [string, () => void][]).map(([label, run]) => (
            <button
              key={label}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={run}
              className="px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-secondary whitespace-nowrap"
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => chain().deleteTable().run()}
            className="px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-destructive hover:bg-destructive/10 whitespace-nowrap"
          >
            Delete table
          </button>
        </div>
      )}

      {/* No https address on this server: the upload is off, and says why. */}
      {!uploadAvailable && (
        <p data-upload-off className="px-2 py-1 border-b border-border text-[11px] text-muted-foreground">
          {UPLOAD_OFF}
        </p>
      )}

      {/* A refused upload says what failed and what to do (the server's words). */}
      {uploadError && (
        <p role="alert" className="px-2 py-1 border-b border-border text-[11px] text-destructive">
          {uploadError}
        </p>
      )}

      {/* Editor body (styles in globals.css under .sig-rich) */}
      <div className="sig-rich">
        <EditorContent editor={editor} />
      </div>

      {/* Raw-HTML source view — full control, shares the same HTML string. */}
      <details
        className="border-t border-border"
        onToggle={(e) => {
          // Entering source view: make sure it shows the editor's latest HTML.
          if ((e.target as HTMLDetailsElement).open) {
            const html = editor.isEmpty ? "" : editor.getHTML();
            if (html !== value) {
              lastEmitted.current = html;
              onChange(html);
            }
          }
        }}
      >
        <summary className="flex cursor-pointer items-center gap-1 px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground select-none">
          <Icon name="Code2" size={12} /> Edit HTML source
          <Icon name="Eye" size={12} className="ml-auto opacity-60" />
        </summary>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          placeholder="<table>…full HTML signature…</table>"
          className="w-full min-h-[120px] max-h-[280px] px-3 py-2 text-xs font-mono text-foreground bg-background outline-none resize-y"
        />
      </details>
    </div>
  );
}
