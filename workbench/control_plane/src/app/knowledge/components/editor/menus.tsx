"use client";

/**
 * The menus of the Knowledge editor that are not suggestion lists (WS-61
 * KB-2b): the bubble menu over a selection, the table tools, the link
 * control and the image dialog.
 *
 * Every control is a shared primitive: `Button` with `selected` for a mark
 * that is on, `Input` for an address, and `Modal` for the dialog.
 *
 * ⚠️ The link control opens from its button in the bubble menu, and no key
 * opens it. `Mod-k` belongs to the shell's command bar (D87), and
 * `src/lib/shell/seams.test.ts` fails a ⌘K handler in this app.
 */
import { useMemo, useState } from "react";
import type { Editor } from "@tiptap/core";
import { BubbleMenu } from "@tiptap/react/menus";
import { useEditorState } from "@tiptap/react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import { matchPages, pageLinkHref, linkedPageId, type PageChoice } from "../../lib/editor/pageLinks";

// ── The bubble menu ─────────────────────────────────────────────────────────

const MARKS = [
  { mark: "bold", label: "Bold", icon: "Bold", run: (e: Editor) => e.chain().focus().toggleBold().run() },
  { mark: "italic", label: "Italic", icon: "Italic", run: (e: Editor) => e.chain().focus().toggleItalic().run() },
  { mark: "strike", label: "Strikethrough", icon: "Strikethrough", run: (e: Editor) => e.chain().focus().toggleStrike().run() },
  { mark: "code", label: "Inline code", icon: "Code", run: (e: Editor) => e.chain().focus().toggleCode().run() },
] as const;

const BLOCK_TYPES = [
  { id: "paragraph", label: "Text", icon: "Pilcrow", level: 0 },
  { id: "h1", label: "Heading 1", icon: "Heading1", level: 1 },
  { id: "h2", label: "Heading 2", icon: "Heading2", level: 2 },
  { id: "h3", label: "Heading 3", icon: "Heading3", level: 3 },
] as const;

/** Show the format menu over a text selection, outside code and tables' cells select. */
function showsFormat(editor: Editor, from: number, to: number): boolean {
  if (!editor.isEditable || from === to) return false;
  if (editor.isActive("codeBlock") || editor.isActive("htmlBlock")) return false;
  const sel = editor.state.selection as { node?: unknown; $anchorCell?: unknown };
  return !sel.node && !sel.$anchorCell;
}

export function FormatBubble({
  editor,
  pages,
  pagesLoading,
  loadPages,
  currentPageId,
}: {
  editor: Editor;
  pages: readonly PageChoice[] | null;
  pagesLoading: boolean;
  loadPages: () => void;
  currentPageId?: string | null;
}) {
  const [linking, setLinking] = useState(false);
  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      strike: e.isActive("strike"),
      code: e.isActive("code"),
      link: e.isActive("link"),
      href: (e.getAttributes("link").href as string | undefined) ?? "",
      paragraph: e.isActive("paragraph"),
      h1: e.isActive("heading", { level: 1 }),
      h2: e.isActive("heading", { level: 2 }),
      h3: e.isActive("heading", { level: 3 }),
    }),
  });

  return (
    <BubbleMenu
      editor={editor}
      pluginKey="kbFormat"
      shouldShow={({ editor: e, from, to }) => showsFormat(e, from, to)}
      options={{ placement: "top", onHide: () => setLinking(false) }}
      className="kb-bubble shadow-md"
      role="toolbar"
      aria-label="Format the selection"
    >
      {linking ? (
        <LinkPanel
          editor={editor}
          initialHref={active?.href ?? ""}
          pages={pages}
          pagesLoading={pagesLoading}
          currentPageId={currentPageId}
          onDone={() => setLinking(false)}
        />
      ) : (
        // A press on a button keeps the focus and the selection in the
        // editor, so the next key a member types lands in the text.
        <div className="flex flex-wrap items-center gap-0.5" onMouseDown={(e) => e.preventDefault()}>
          {MARKS.map((m) => (
            <Button
              key={m.mark}
              variant="ghost"
              size="icon-sm"
              icon={m.icon}
              aria-label={m.label}
              title={m.label}
              selected={Boolean(active?.[m.mark])}
              onClick={() => m.run(editor)}
            />
          ))}
          <Button
            variant="ghost"
            size="icon-sm"
            icon="Link"
            aria-label="Link"
            title="Link"
            selected={Boolean(active?.link)}
            onClick={() => {
              loadPages();
              setLinking(true);
            }}
          />
          <span className="mx-1 h-5 w-px bg-border" aria-hidden />
          {BLOCK_TYPES.map((b) => (
            <Button
              key={b.id}
              variant="ghost"
              size="icon-sm"
              icon={b.icon}
              aria-label={b.label}
              title={b.label}
              selected={Boolean(active?.[b.id])}
              onClick={() =>
                b.level === 0
                  ? editor.chain().focus().setParagraph().run()
                  : editor.chain().focus().toggleHeading({ level: b.level }).run()
              }
            />
          ))}
        </div>
      )}
    </BubbleMenu>
  );
}

// ── The link control ────────────────────────────────────────────────────────

/** True when `text` reads as an address a member typed, not as page words. */
export function looksLikeAddress(text: string): boolean {
  const t = text.trim();
  return /^(?:https?:\/\/|mailto:|\/|#)/i.test(t) || /^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(t);
}

/** The address a typed text links to: a bare domain gets `https://`. */
export function addressOf(text: string): string {
  const t = text.trim();
  if (/^(?:[a-z][a-z0-9+.-]*:|\/|#)/i.test(t)) return t;
  return `https://${t}`;
}

/**
 * One field for an address or a page. Words that are not an address search
 * the member's pages, and a pick writes `/knowledge/pages/<id>`.
 */
export function LinkPanel({
  editor,
  initialHref,
  pages,
  pagesLoading,
  currentPageId,
  onDone,
}: {
  editor: Editor;
  initialHref: string;
  pages: readonly PageChoice[] | null;
  pagesLoading: boolean;
  currentPageId?: string | null;
  onDone: () => void;
}) {
  const linked = linkedPageId(initialHref);
  const linkedTitle = linked ? pages?.find((p) => p.id === linked)?.title : null;
  const [text, setText] = useState(linkedTitle ?? initialHref);
  const [active, setActive] = useState(0);
  const address = looksLikeAddress(text);
  const rows = useMemo(
    () => (address || !pages ? [] : matchPages(pages, text, { exclude: currentPageId, limit: 5 })),
    [address, pages, text, currentPageId],
  );

  const apply = (href: string) => {
    editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
    onDone();
  };
  const submit = () => {
    const row = rows[active];
    if (row) apply(pageLinkHref(row.id));
    else if (text.trim()) apply(addressOf(text));
  };

  return (
    <div className="w-72 max-w-[calc(100vw-2rem)] space-y-1 p-1">
      <div className="flex items-center gap-1">
        <Input
          autoFocus
          aria-label="Link address or page"
          placeholder="Paste a link, or search your pages"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onDone();
              editor.commands.focus();
            } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && rows.length) {
              e.preventDefault();
              setActive((a) => (a + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length);
            }
          }}
          className="min-w-0 flex-1"
        />
        <Button variant="secondary" size="sm" disabled={!text.trim()} onClick={submit}>
          Apply
        </Button>
        {initialHref ? (
          <Button
            variant="ghost"
            size="icon-sm"
            icon="Unlink"
            aria-label="Remove the link"
            title="Remove the link"
            onClick={() => {
              editor.chain().focus().extendMarkRange("link").unsetLink().run();
              onDone();
            }}
          />
        ) : null}
      </div>
      {!address && text.trim() ? (
        <div role="listbox" aria-label="Pages" className="space-y-0.5">
          {pagesLoading && !pages ? <p className="px-2 py-1 text-[11px] text-muted-foreground">Loading pages…</p> : null}
          {pages && rows.length === 0 ? (
            <p className="px-2 py-1 text-[11px] text-muted-foreground">No page has that title. Enter links it as an address.</p>
          ) : null}
          {rows.map((p, i) => (
            <Button
              key={p.id}
              role="option"
              aria-selected={i === active}
              variant="ghost"
              size="sm"
              icon="FileText"
              layout="flex w-full items-center justify-start gap-2"
              selected={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => apply(pageLinkHref(p.id))}
            >
              <span className="min-w-0 flex-1 truncate text-left">{p.title}</span>
              <span className="shrink-0 text-[11px] text-muted-foreground">{p.spaceName}</span>
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ── The table tools ─────────────────────────────────────────────────────────

const TABLE_ACTS = [
  { label: "Add a row below", icon: "BetweenHorizontalEnd", run: (e: Editor) => e.chain().focus().addRowAfter().run() },
  { label: "Remove this row", icon: "Rows3", run: (e: Editor) => e.chain().focus().deleteRow().run() },
  { label: "Add a column after", icon: "BetweenVerticalEnd", run: (e: Editor) => e.chain().focus().addColumnAfter().run() },
  { label: "Remove this column", icon: "Columns3", run: (e: Editor) => e.chain().focus().deleteColumn().run() },
  { label: "Turn the header row on or off", icon: "PanelTop", run: (e: Editor) => e.chain().focus().toggleHeaderRow().run() },
] as const;

/** The table tools, over the caret while it is in a table. */
export function TableTools({ editor }: { editor: Editor }) {
  return (
    <BubbleMenu
      editor={editor}
      pluginKey="kbTable"
      shouldShow={({ editor: e, from, to }) => e.isEditable && e.isActive("table") && from === to}
      options={{ placement: "top-start" }}
      className="kb-bubble shadow-md"
      role="toolbar"
      aria-label="Table"
    >
      <div className="flex items-center gap-0.5" onMouseDown={(e) => e.preventDefault()}>
        {TABLE_ACTS.map((a) => (
          <Button
            key={a.label}
            variant="ghost"
            size="icon-sm"
            icon={a.icon}
            aria-label={a.label}
            title={a.label}
            onClick={() => a.run(editor)}
          />
        ))}
        <span className="mx-1 h-5 w-px bg-border" aria-hidden />
        <Button
          variant="ghost"
          size="icon-sm"
          icon="Trash2"
          aria-label="Delete the table"
          title="Delete the table"
          onClick={() => editor.chain().focus().deleteTable().run()}
        />
      </div>
    </BubbleMenu>
  );
}

// ── The image dialog ────────────────────────────────────────────────────────

/** Ask for the address and the words of an image, then insert it. */
export function ImageDialog({
  open,
  onClose,
  onInsert,
}: {
  open: boolean;
  onClose: () => void;
  onInsert: (image: { src: string; alt: string }) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Add an image" icon="Image" size="sm" closeLabel="Cancel">
      {open ? <ImageForm onClose={onClose} onInsert={onInsert} /> : null}
    </Modal>
  );
}

function ImageForm({
  onClose,
  onInsert,
}: {
  onClose: () => void;
  onInsert: (image: { src: string; alt: string }) => void;
}) {
  const [src, setSrc] = useState("");
  const [alt, setAlt] = useState("");
  return (
    <form
      className="contents"
      onSubmit={(e) => {
        e.preventDefault();
        if (src.trim()) onInsert({ src: src.trim(), alt: alt.trim() });
      }}
    >
      <div className="space-y-3 p-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-foreground">Image address</span>
          <Input
            autoFocus
            value={src}
            onChange={(e) => setSrc(e.target.value)}
            placeholder="https://example.com/chart.png"
            className="w-full"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-foreground">What the image shows</span>
          <Input value={alt} onChange={(e) => setAlt(e.target.value)} placeholder="For example: Refunds by month" className="w-full" />
        </label>
        <p className="text-xs text-muted-foreground">
          An image from another site shows after a reader chooses Load image.
        </p>
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!src.trim()}>
          Add image
        </Button>
      </footer>
    </form>
  );
}
