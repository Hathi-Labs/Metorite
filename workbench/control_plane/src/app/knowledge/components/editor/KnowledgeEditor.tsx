"use client";

/**
 * `KnowledgeEditor` — the one renderer of a Knowledge page (WS-61 KB-2b,
 * `knowledge_base.md` §21.4).
 *
 * The page view reads the body through it, read-only, and Edit opens the
 * same editor in place. So what a member reads is exactly what they edit.
 *
 * - The stored body stays Markdown. The editor parses it with the one
 *   manager of `lib/editor/markdown.ts`, and writes Markdown back on each
 *   change. When the member changed nothing that Markdown keeps, the draft
 *   keeps the stored body byte for byte, so a save with no edit writes
 *   nothing (`bodyAfterEdit`).
 * - `/` opens the block menu, and `[[` opens the page picker. Both draw
 *   through `OptionList` from `src/components/ui/`.
 * - A selection raises the bubble menu: the marks, the link control and the
 *   block type. The caret in a table raises the table tools.
 * - A handle beside each block drags it (`@tiptap/extension-drag-handle`).
 * - Markdown shortcuts work as you type: `# `, `- `, `1. `, `> `, `[ ] `,
 *   three backticks, `---`, `**bold**`.
 *
 * ⚠️ Nothing here binds `Mod-k` or listens for ⌘K. The shell owns the command
 * bar (D87), and `src/lib/shell/seams.test.ts` fails a ⌘K handler here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Extension, type Editor, type Range } from "@tiptap/core";
import { PluginKey } from "@tiptap/pm/state";
import { EditorContent, ReactNodeViewRenderer, useEditor } from "@tiptap/react";
import Suggestion from "@tiptap/suggestion";
import { DragHandle } from "@tiptap/extension-drag-handle-react";

import Icon from "@/components/Icon";
import { OptionList } from "@/components/ui/OptionList";
import { isInAppPath } from "@/lib/inAppLink";

import { listPages } from "../../lib/api";
import { matchBlocks, type BlockItem } from "../../lib/editor/blocks";
import { bodyAfterEdit, normaliseBody, parseBody, writeBody } from "../../lib/editor/markdown";
import { loadPageChoices, matchPages, pageLinkHref, type PageChoice } from "../../lib/editor/pageLinks";
import { knowledgeExtensions } from "../../lib/editor/schema";
import type { Space } from "../../lib/types";
import { FormatBubble, ImageDialog, TableTools, scrollParent } from "./menus";
import { CalloutView, CodeBlockView, DetailsView, ImageView } from "./nodeViews";
import { useSuggestionMenu, type SuggestionMenu } from "./useSuggestionMenu";

/** The hint on an empty body while a member writes. */
export const EDITOR_PLACEHOLDER = "Write here. Type / for a block, or [[ to link a page.";

/** True when the typed trigger sits in a code block or in raw HTML. */
function inCode(editor: Editor, pos: number): boolean {
  return Boolean(editor.state.doc.resolve(pos).parent.type.spec.code);
}

interface MenuHandles {
  slash: () => ReturnType<SuggestionMenu<BlockItem>["render"]>;
  pages: () => ReturnType<SuggestionMenu<PageChoice>["render"]>;
  onSpecial: (item: BlockItem, editor: Editor, range: Range) => void;
}

declare module "@tiptap/core" {
  interface Storage {
    knowledgeMenus: MenuStorage;
  }
}

/**
 * The `/` menu and the `[[` picker, as one extension. The component puts its
 * menu handles in the extension's storage once the editor exists, and the
 * plugins read them when a menu opens.
 */
type MenuRenderer = ReturnType<MenuHandles["slash"]> | ReturnType<MenuHandles["pages"]>;

/**
 * A renderer that finds the component's menu when each event comes.
 * `@tiptap/suggestion` asks for the renderer when the editor starts, which
 * is before the component has set its handles.
 */
function lateRenderer<P, K>(get: () => MenuRenderer | null) {
  return {
    onStart: (props: P) => (get()?.onStart as ((p: P) => void) | undefined)?.(props),
    onUpdate: (props: P) => (get()?.onUpdate as ((p: P) => void) | undefined)?.(props),
    onKeyDown: (props: K) => (get()?.onKeyDown as ((p: K) => boolean) | undefined)?.(props) ?? false,
    onExit: () => get()?.onExit(),
  };
}

interface MenuStorage {
  handles: MenuHandles | null;
  /** Called by the component after each render. */
  setHandles: (handles: MenuHandles) => void;
}

const KnowledgeMenus = Extension.create<object, MenuStorage>({
  name: "knowledgeMenus",
  addStorage() {
    return {
      handles: null,
      setHandles(this: MenuStorage, handles: MenuHandles) {
        this.handles = handles;
      },
    };
  },
  addProseMirrorPlugins() {
    // Read through the editor at call time, so the plugins see the handles
    // that the component set on this editor's own storage.
    const editor = this.editor;
    const handles = () => editor.storage.knowledgeMenus.handles;
    return [
      Suggestion<BlockItem, BlockItem>({
        editor: this.editor,
        char: "/",
        // Block names have spaces ("Heading 1", "Note callout").
        allowSpaces: true,
        pluginKey: new PluginKey("kbSlash"),
        allow: ({ editor, range }) => editor.isEditable && !inCode(editor, range.from),
        items: () => [],
        command: ({ editor, range, props }) => {
          if (props.kind === "insert") props.run?.(editor, range);
          else handles()?.onSpecial(props, editor, range);
        },
        render: () => lateRenderer(() => handles()?.slash() ?? null),
      }),
      Suggestion<PageChoice, PageChoice>({
        editor: this.editor,
        char: "[[",
        allowSpaces: true,
        pluginKey: new PluginKey("kbPageLink"),
        allow: ({ editor, range }) => editor.isEditable && !inCode(editor, range.from),
        items: () => [],
        command: ({ editor, range, props }) => {
          editor
            .chain()
            .focus()
            .deleteRange(range)
            .insertContent([
              { type: "text", text: props.title, marks: [{ type: "link", attrs: { href: pageLinkHref(props.id) } }] },
              { type: "text", text: " " },
            ])
            .run();
        },
        render: () => lateRenderer(() => handles()?.pages() ?? null),
      }),
    ];
  },
});

export interface KnowledgeEditorProps {
  /** Read: the stored body. Edit: the draft body when the editor opens. */
  body: string;
  mode: "read" | "edit";
  /** Edit: the body the edit began with, for the no-edit rule. */
  storedBody?: string;
  /** Edit: each change, as the body a save would write. */
  onChange?: (body: string) => void;
  /** Edit: the open page, which the page picker leaves out. */
  pageId?: string | null;
  /** Edit: the spaces whose pages the picker lists. */
  spaces?: readonly Space[];
  /** The name of the body for a screen reader. */
  label: string;
  /** Read: a click on a link to a page of this app. */
  onNavigate?: (href: string) => void;
}

export default function KnowledgeEditor({
  body,
  mode,
  storedBody,
  onChange,
  pageId,
  spaces,
  label,
  onNavigate,
}: KnowledgeEditorProps) {
  const editable = mode === "edit";
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  // What the editor writes for the stored body when nothing changed.
  const stored = storedBody ?? body;
  const baseline = useMemo(() => normaliseBody(stored), [stored]);
  const baselineRef = useRef({ stored, baseline });
  useEffect(() => {
    baselineRef.current = { stored, baseline };
  }, [stored, baseline]);

  // ── The member's pages, for `[[` and the link control ──────────────────
  const [pages, setPages] = useState<PageChoice[] | null>(null);
  const [pagesLoading, setPagesLoading] = useState(false);
  const spacesRef = useRef(spaces ?? []);
  useEffect(() => {
    spacesRef.current = spaces ?? [];
  }, [spaces]);
  const loadPages = useCallback(() => {
    if (pages || pagesLoading) return;
    setPagesLoading(true);
    void loadPageChoices(spacesRef.current, listPages)
      .then(setPages)
      .catch(() => setPages([]))
      .finally(() => setPagesLoading(false));
  }, [pages, pagesLoading]);

  const slash = useSuggestionMenu<BlockItem>((q) => matchBlocks(q));
  const pageRows = useCallback((q: string) => matchPages(pages ?? [], q, { exclude: pageId }), [pages, pageId]);
  const pageMenu = useSuggestionMenu<PageChoice>(pageRows);

  const [imageAt, setImageAt] = useState<Range | null>(null);
  const editorRef = useRef<Editor | null>(null);

  const extensions = useMemo(
    () => [
      ...knowledgeExtensions({
        placeholder: EDITOR_PLACEHOLDER,
        views: {
          image: ReactNodeViewRenderer(ImageView),
          codeBlock: ReactNodeViewRenderer(CodeBlockView),
          callout: ReactNodeViewRenderer(CalloutView),
          details: ReactNodeViewRenderer(DetailsView),
        },
      }),
      KnowledgeMenus,
    ],
    [],
  );

  const editor = useEditor(
    {
      immediatelyRender: false,
      editable,
      extensions,
      content: parseBody(body),
      editorProps: {
        attributes: editable
          ? {
              class: "kb-editor cc-prose prose prose-sm max-w-none",
              role: "textbox",
              "aria-multiline": "true",
              "aria-label": label,
            }
          : { class: "kb-editor cc-prose prose prose-sm max-w-none", "aria-label": label },
      },
      onUpdate: ({ editor: e }) => {
        const { stored: s, baseline: b } = baselineRef.current;
        onChangeRef.current?.(bodyAfterEdit(s, b, writeBody(e.getJSON())));
      },
    },
    // A new editor for each mode. In edit mode the parent keys each session.
    [editable],
  );

  useEffect(() => {
    editorRef.current = editor;
  }, [editor]);

  // The box that scrolls the page, so the floating menus follow a scroll.
  const [scroller, setScroller] = useState<HTMLElement | Window | undefined>(undefined);
  const shellRef = useCallback((el: HTMLDivElement | null) => {
    if (el) setScroller(scrollParent(el));
  }, []);

  // The menus' handles, for the plugins of `KnowledgeMenus`.
  useEffect(() => {
    if (!editor) return;
    editor.storage.knowledgeMenus.setHandles({
      slash: slash.render,
      pages: () => {
        const r = pageMenu.render();
        return {
          ...r,
          onStart: (props) => {
            loadPages();
            r.onStart(props);
          },
        };
      },
      onSpecial: (item, e, range) => {
        if (item.kind === "image") {
          e.chain().focus().deleteRange(range).run();
          setImageAt({ from: range.from, to: range.from });
        } else if (item.kind === "pageLink") {
          e.chain().focus().deleteRange(range).insertContent("[[").run();
        }
      },
    });
  });

  // The read view follows the stored body: a save, a revert, a reload.
  const shown = useRef(body);
  useEffect(() => {
    if (!editor || editable || shown.current === body) return;
    shown.current = body;
    editor.commands.setContent(parseBody(body), { emitUpdate: false });
  }, [editor, editable, body]);

  // A link in the read view: a page of this app opens in place, and any
  // other address opens in a new tab. A modified click keeps the browser's
  // own meaning.
  const onClick = useCallback(
    (e: React.MouseEvent) => {
      if (editable) return;
      const a = (e.target as HTMLElement).closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      const href = a.getAttribute("href") ?? "";
      if (!href || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (isInAppPath(href)) onNavigate?.(href);
      else window.open(a.href, "_blank", "noopener,noreferrer");
    },
    [editable, onNavigate],
  );

  const slashRows = slash.state ? matchBlocks(slash.state.query) : [];
  const pickRows = pageMenu.state ? pageRows(pageMenu.state.query) : [];
  const slashAnchor = slash.state?.rect ? { getBoundingClientRect: () => slash.state!.rect!() ?? new DOMRect() } : null;
  const pageAnchor = pageMenu.state?.rect
    ? { getBoundingClientRect: () => pageMenu.state!.rect!() ?? new DOMRect() }
    : null;

  return (
    <div ref={shellRef} className={editable ? "kb-editor-shell is-editing" : "kb-editor-shell"} onClick={onClick}>
      {editor && editable ? (
        <DragHandle editor={editor} className="kb-drag-handle">
          <span className="kb-drag-grip" title="Drag to move this block">
            <Icon name="GripVertical" size={14} />
          </span>
        </DragHandle>
      ) : null}
      <EditorContent editor={editor} />
      {editor && editable ? (
        <>
          <FormatBubble
            editor={editor}
            pages={pages}
            pagesLoading={pagesLoading}
            loadPages={loadPages}
            currentPageId={pageId}
            scrollTarget={scroller}
          />
          <TableTools editor={editor} scrollTarget={scroller} />
          <OptionList
            id="kb-slash-menu"
            label="Blocks"
            anchor={slashAnchor}
            open={Boolean(slash.state)}
            rows={slashRows.map((b) => ({ id: b.id, label: b.label, icon: b.icon }))}
            active={slash.state?.active ?? 0}
            onPick={(i) => slashRows[i] && slash.pick(slashRows[i])}
            onHover={slash.setActive}
            emptyText="No block has that name."
          />
          <OptionList
            id="kb-page-menu"
            label="Pages"
            anchor={pageAnchor}
            open={Boolean(pageMenu.state)}
            rows={pickRows.map((p) => ({ id: p.id, label: p.title, hint: p.spaceName, icon: "FileText" }))}
            active={pageMenu.state?.active ?? 0}
            onPick={(i) => pickRows[i] && pageMenu.pick(pickRows[i])}
            onHover={pageMenu.setActive}
            status={pagesLoading && !pages ? "Loading pages…" : null}
            emptyText={pageMenu.state?.query ? "No page has that title." : "No pages yet."}
          />
          <ImageDialog
            open={imageAt !== null}
            onClose={() => setImageAt(null)}
            onInsert={({ src, alt }) => {
              const at = imageAt;
              setImageAt(null);
              const e = editorRef.current;
              if (!e || !at) return;
              e.chain().focus().insertContentAt(at.from, { type: "image", attrs: { src, alt } }).run();
            }}
          />
        </>
      ) : null}
      {editable && slash.state ? (
        <span className="sr-only" aria-live="polite">
          {slashRows[slash.state.active]?.label ?? ""}
        </span>
      ) : null}
    </div>
  );
}
