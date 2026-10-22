"use client";

/**
 * `KnowledgeEditor` — the one renderer of a Knowledge page (WS-61 KB-2b,
 * `knowledge_base.md` §21.4).
 *
 * The page view reads the body through it, read-only, and Edit opens the
 * same editor in place. So what a member reads is exactly what they edit.
 *
 * - The stored body stays Markdown. The editor parses it ONCE, when it
 *   mounts, with the one manager of `lib/editor/markdown.ts`. A keystroke
 *   parses nothing and writes nothing: the write waits for a pause in the
 *   typing (`WRITE_AFTER_MS`). `flushRef` writes at once, and the page calls
 *   it before a save, a leave and a switch to the Markdown field. When the
 *   member changed nothing that Markdown keeps, the draft keeps the stored
 *   body byte for byte, so a save with no edit writes nothing
 *   (`bodyAfterEdit`). Fix round P1: a parse and a write on each key cost
 *   170 ms at 19 KB and 2 s at 75 KB.
 * - `/` opens the block menu, grouped as in Notion. `@` opens the mention
 *   menu: Pages, then Date. `[[` still opens the page picker for old habits,
 *   and the slash item "Link to a page" opens `@`. They all draw through
 *   `OptionList` from `src/components/ui/`. A page pick writes the Markdown
 *   link `[Title](/knowledge/pages/<id>)`, as `[[` always did (KB-2h).
 * - A link to a page draws as a mention chip (`pageChips.ts`), in the read
 *   view and in the editor.
 * - A selection raises the bubble menu: the marks, the link control and the
 *   block type. The caret in a table raises the table tools.
 * - A handle beside each block drags it (`@tiptap/extension-drag-handle`).
 * - KB-2i (§21.7): grips on a table's rows and columns (`TableHandles`),
 *   find and replace on Mod-F (`FindBar`), the `:` emoji insert, and the
 *   title flow (`TitleField`, `titleLink`).
 * - Markdown shortcuts work as you type: `# `, `- `, `1. `, `> `, `[ ] `,
 *   three backticks, `---`, `**bold**`.
 *
 * ⚠️ Nothing here binds `Mod-k` or listens for ⌘K. Find takes Mod-F only. The shell owns the command
 * bar (D87), and `src/lib/shell/seams.test.ts` fails a ⌘K handler here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Extension, type Editor, type Range } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { PluginKey, Selection } from "@tiptap/pm/state";
import type { DecorationSet } from "@tiptap/pm/view";
import { EditorContent, ReactNodeViewRenderer, useEditor } from "@tiptap/react";
import Suggestion, { findSuggestionMatch } from "@tiptap/suggestion";

import Icon from "@/components/Icon";
import { OptionList, optionId } from "@/components/ui/OptionList";
import { isInAppPath } from "@/lib/inAppLink";

import { listPages } from "../../lib/api";
import { dateLabel } from "../../lib/editor/date";
import { MENTION_PREFIXES, buildMentionItems, type MentionItem } from "../../lib/editor/mention";
import { PageChips, chipRuns } from "../../lib/editor/pageChips";
import { matchBlocks, type BlockItem } from "../../lib/editor/blocks";
import { SearchAndReplace } from "../../lib/editor/searchAndReplace";
import { TitleHandoff, isEmptyBody } from "../../lib/editor/titleFlow";
import { loadEmoji, searchEmoji, type EmojiMatch } from "../../lib/emoji";
import { createBodyWriter, type BodyWriter } from "../../lib/editor/bodyWriter";
import { bodyAfterEdit, markdownWork, normaliseBody, parseBody, writeBody } from "../../lib/editor/markdown";
import { loadPageChoices, matchPages, pageLinkHref, type PageChoice } from "../../lib/editor/pageLinks";
import { knowledgeExtensions } from "../../lib/editor/schema";
import type { Space } from "../../lib/types";
import { FindBar } from "./FindBar";
import { FormatBubble, ImageDialog, TableTools, scrollParent } from "./menus";
import { CalloutView, CodeBlockView, DetailsView, ImageView } from "./nodeViews";
import { TableHandlePlugin } from "./tableDrag";
import { TableHandles } from "./TableHandles";
import type { TitleLink } from "./TitleField";
import { storedFormViews } from "./storedFormViews";
import { TableCellTools } from "./TableExtras";
import { useKnowledge } from "../KnowledgeContext";
import { useSuggestionMenu, type SuggestionMenu } from "./useSuggestionMenu";

/**
 * The block handle, loaded only for an edit. Its package brings the Yjs
 * collaboration code with it, and a reader needs none of that.
 */
const DragHandle = dynamic(() => import("@tiptap/extension-drag-handle-react").then((m) => m.DragHandle), {
  ssr: false,
});

/** How long the editor waits after the last key before it writes Markdown. */
export const WRITE_AFTER_MS = 300;

/** The hint on an empty body while a member writes. */
export const EDITOR_PLACEHOLDER = "Write here. Type / for a block, or @ to link a page.";

/** True when the typed trigger sits in a code block or in raw HTML. */
function inCode(editor: Editor, pos: number): boolean {
  return Boolean(editor.state.doc.resolve(pos).parent.type.spec.code);
}

interface MenuHandles {
  slash: () => ReturnType<SuggestionMenu<BlockItem>["render"]>;
  pages: () => ReturnType<SuggestionMenu<PageChoice>["render"]>;
  mention: () => ReturnType<SuggestionMenu<MentionItem>["render"]>;
  emoji: () => ReturnType<SuggestionMenu<EmojiMatch>["render"]>;
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
type MenuRenderer =
  | ReturnType<MenuHandles["slash"]>
  | ReturnType<MenuHandles["pages"]>
  | ReturnType<MenuHandles["mention"]>
  | ReturnType<MenuHandles["emoji"]>;

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
      // `@` is Notion's mention: a page, or a date. It opens at the start of a
      // line or after a space, so `a@b.com` opens nothing. The query may hold
      // spaces (a page title), but an `@` then a space closes it.
      Suggestion<MentionItem, MentionItem>({
        editor: this.editor,
        char: "@",
        allowSpaces: true,
        allowedPrefixes: MENTION_PREFIXES,
        pluginKey: new PluginKey("kbMention"),
        allow: ({ editor, range }) => editor.isEditable && !inCode(editor, range.from),
        findSuggestionMatch: (config) => {
          const match = findSuggestionMatch(config);
          return match && /^\s/.test(match.query) ? null : match;
        },
        items: () => [],
        command: ({ editor, range, props }) => {
          const chain = editor.chain().focus().deleteRange(range);
          if (props.kind === "page") {
            chain
              .insertContent([
                { type: "text", text: props.page.title, marks: [{ type: "link", attrs: { href: pageLinkHref(props.page.id) } }] },
                { type: "text", text: " " },
              ])
              .run();
          } else {
            chain
              .insertContent([
                { type: "kbDate", attrs: { date: props.iso, label: dateLabel(props.iso) } },
                { type: "text", text: " " },
              ])
              .run();
          }
        },
        render: () => lateRenderer(() => handles()?.mention() ?? null),
      }),
      // `:` and a name inserts an emoji, as in Docmost: the emoji and a space.
      // The `:` must start a word, so "10:30" and "Note:" open nothing.
      Suggestion<EmojiMatch, EmojiMatch>({
        editor: this.editor,
        char: ":",
        pluginKey: new PluginKey("kbEmoji"),
        allow: ({ editor, range }) => editor.isEditable && !inCode(editor, range.from),
        items: () => [],
        command: ({ editor, range, props }) => {
          editor.chain().focus().deleteRange(range).insertContent(`${props.char} `).run();
        },
        render: () => lateRenderer(() => handles()?.emoji() ?? null),
      }),
    ];
  },
});

declare module "@tiptap/core" {
  interface Storage {
    kbFindKeys: { open: (() => void) | null; close: (() => boolean) | null };
  }
}

/**
 * Mod-F opens find in the page, and Escape closes it. Docmost binds the same
 * keys. ⚠️ Mod-F, never Mod-K: the shell owns ⌘K (D87).
 */
const FindKeys = Extension.create<object, { open: (() => void) | null; close: (() => boolean) | null }>({
  name: "kbFindKeys",
  addStorage() {
    return { open: null, close: null };
  },
  addKeyboardShortcuts() {
    return {
      "Mod-f": () => {
        this.storage.open?.();
        return Boolean(this.storage.open);
      },
      Escape: () => this.storage.close?.() ?? false,
    };
  },
});

export interface KnowledgeEditorProps {
  /** Read: the stored body. Edit: the draft body when the editor opens. */
  body: string;
  mode: "read" | "edit";
  /** Edit: the body the edit began with, for the no-edit rule. */
  storedBody?: string;
  /**
   * Edit: the body a save would write. It comes after a pause in the typing,
   * and on each flush.
   */
  onChange?: (body: string) => void;
  /** Edit: the first key after a write. The page marks the draft dirty. */
  onTouched?: () => void;
  /**
   * Edit: the editor puts its flush here. A flush writes the body at once,
   * calls `onChange`, and returns the body. A `silent` flush returns the body
   * and calls nothing, for a caller that must not set state.
   */
  flushRef?: React.RefObject<((opts?: { silent?: boolean }) => string | null) | null>;
  /** Edit: the open page, which the page picker leaves out. */
  pageId?: string | null;
  /** Edit: the spaces whose pages the picker lists. */
  spaces?: readonly Space[];
  /** The name of the body for a screen reader. */
  label: string;
  /** Read: a click on a link to a page of this app. */
  onNavigate?: (href: string) => void;
  /**
   * Read: marks drawn over the body, such as the changes of a compare
   * (KB-2g, `lib/historyDiff.ts`). The editor draws them and stores nothing.
   */
  decorate?: ((doc: PMNode) => DecorationSet | null) | null;
  /** Edit: the page's title field, for the title flow (KB-2i). */
  titleLink?: TitleLink;
}

export default function KnowledgeEditor({
  body,
  mode,
  storedBody,
  onChange,
  onTouched,
  flushRef,
  pageId,
  spaces,
  label,
  onNavigate,
  decorate,
  titleLink,
}: KnowledgeEditorProps) {
  const editable = mode === "edit";
  const onChangeRef = useRef(onChange);
  const onTouchedRef = useRef(onTouched);
  const decorateRef = useRef(decorate);
  useEffect(() => {
    onChangeRef.current = onChange;
    onTouchedRef.current = onTouched;
  });

  // The document, parsed once for this editor. A later `body` prop is the
  // draft the editor itself wrote, so it needs no parse.
  const [initialDoc] = useState(() => parseBody(body));

  // What the editor writes for the stored body when nothing changed. It is
  // worked out at the first write, not at mount.
  const stored = storedBody ?? body;
  const baselineRef = useRef<{ stored: string; opened: string; baseline: string | null }>({
    stored,
    opened: body,
    baseline: null,
  });

  // ── The write: after a pause, or at once on a flush ─────────────────────
  const editorRef = useRef<Editor | null>(null);
  const writerRef = useRef<BodyWriter | null>(null);

  // ── The member's pages, for `[[` and the link control ──────────────────
  const [pages, setPages] = useState<PageChoice[] | null>(null);
  const [pagesLoading, setPagesLoading] = useState(false);
  // The read view has no `spaces` prop, so it takes the layout's list. The
  // list gives the chips their page emoji.
  const known = useKnowledge().spaces;
  const spaceList = spaces ?? known;
  const spacesRef = useRef(spaceList);
  useEffect(() => {
    spacesRef.current = spaceList;
  }, [spaceList]);
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
  const mentionRows = useCallback(
    (q: string) => buildMentionItems(pages ?? [], q, { exclude: pageId }),
    [pages, pageId],
  );
  const mentionMenu = useSuggestionMenu<MentionItem>(mentionRows);
  const emojiRows = useCallback((q: string) => searchEmoji(q), []);
  // `searchEmoji` answers [] until the data is in memory, so the edit mode
  // loads it at once. Then the first `:` has rows (KB-2f data, KB-2i insert).
  useEffect(() => {
    if (editable) void loadEmoji().catch(() => undefined);
  }, [editable]);
  const emojiMenu = useSuggestionMenu<EmojiMatch>(emojiRows);

  const [imageAt, setImageAt] = useState<Range | null>(null);

  const extensions = useMemo(
    () => [
      ...knowledgeExtensions({
        placeholder: EDITOR_PLACEHOLDER,
        views: {
          image: ReactNodeViewRenderer(ImageView),
          codeBlock: ReactNodeViewRenderer(CodeBlockView),
          callout: ReactNodeViewRenderer(CalloutView),
          details: ReactNodeViewRenderer(DetailsView),
          ...storedFormViews(),
        },
      }),
      KnowledgeMenus,
      PageChips,
      SearchAndReplace,
      FindKeys,
      TableHandlePlugin,
      TitleHandoff,
    ],
    [],
  );

  const editor = useEditor(
    {
      immediatelyRender: false,
      editable,
      extensions,
      content: initialDoc,
      editorProps: {
        attributes: editable
          ? {
              class: "kb-editor cc-prose prose prose-sm max-w-none",
              role: "textbox",
              "aria-multiline": "true",
              "aria-label": label,
            }
          : { class: "kb-editor cc-prose prose prose-sm max-w-none", "aria-label": label },
        decorations: (state) => decorateRef.current?.(state.doc) ?? null,
      },
      // Cheap on purpose: no parse and no write on a key (`bodyWriter.ts`).
      onUpdate: () => writerRef.current?.touch(),
    },
    // A new editor for each mode. In edit mode the parent keys each session.
    [editable],
  );

  useEffect(() => {
    editorRef.current = editor;
  }, [editor]);

  // New marks redraw at once: an empty transaction asks the view again.
  useEffect(() => {
    decorateRef.current = decorate;
    if (editor && !editor.isDestroyed) editor.view.dispatch(editor.state.tr.setMeta("kbDecorate", true));
  }, [editor, decorate]);

  useEffect(() => {
    const writer = createBodyWriter({
      delayMs: WRITE_AFTER_MS,
      serialize: () => {
        const e = editorRef.current;
        if (!e) return null;
        const base = baselineRef.current;
        if (base.baseline === null) {
          base.baseline = base.opened === base.stored ? writeBody(initialDoc) : normaliseBody(base.stored);
        }
        return bodyAfterEdit(base.stored, base.baseline, writeBody(e.getJSON()));
      },
      onChange: (next) => onChangeRef.current?.(next),
      onTouched: () => onTouchedRef.current?.(),
    });
    writerRef.current = writer;
    if (flushRef) flushRef.current = (opts) => writer.flush(opts);
    return () => {
      // A remount (the 767 px frame swap) must not lose the last keys.
      writer.flush();
      writer.dispose();
      if (flushRef) flushRef.current = null;
      writerRef.current = null;
    };
  }, [flushRef, initialDoc]);

  // The e2e spec reads how much Markdown work the editor did.
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") {
      (window as unknown as { __kbMarkdownWork?: typeof markdownWork }).__kbMarkdownWork = markdownWork;
    }
  }, []);

  // The box that scrolls the page, so the floating menus follow a scroll.
  const [scroller, setScroller] = useState<HTMLElement | Window | undefined>(undefined);
  const [shell, setShell] = useState<HTMLDivElement | null>(null);
  const shellRef = useCallback((el: HTMLDivElement | null) => {
    setShell(el);
    if (el) setScroller(scrollParent(el));
  }, []);

  // ── Find and replace (KB-2i) ───────────────────────────────────────────
  // `findOpen` counts the opens, so each Mod-F takes the selected text again.
  const [findOpen, setFindOpen] = useState(0);
  const openFind = useCallback(() => setFindOpen((n) => n + 1), []);
  const closeFind = useCallback(() => setFindOpen(0), []);
  useEffect(() => {
    if (!editor) return;
    editor.storage.kbFindKeys.open = openFind;
    editor.storage.kbFindKeys.close = () => {
      if (!findOpen) return false;
      closeFind();
      return true;
    };
  }, [editor, openFind, closeFind, findOpen]);
  // The read view is not focusable, so Mod-F there comes from the window.
  // It leaves a field outside the body alone, such as the command bar.
  useEffect(() => {
    if (editable) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== "f") return;
      const target = e.target as HTMLElement | null;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
      // A page behind an open modal, such as the history (KB-2g), leaves the
      // key to the editor in the modal. `Modal` marks the page behind it.
      if (shell?.closest("[data-base-ui-inert], [aria-hidden='true']")) return;
      e.preventDefault();
      openFind();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editable, openFind, shell]);

  // ── The title flow (KB-2i) ─────────────────────────────────────────────
  useEffect(() => {
    if (!editor || !editable || !titleLink) return;
    titleLink.body.current = {
      focusStart: () => editor.commands.focus("start"),
      prependParagraph: (text) => {
        const content = text ? [{ type: "text", text }] : undefined;
        // Not in the body's undo history: an undo there keeps the split, as in Docmost.
        editor
          .chain()
          .command(({ tr }) => {
            tr.setMeta("addToHistory", false);
            return true;
          })
          .insertContentAt(isEmptyBody(editor.state) ? { from: 0, to: editor.state.doc.content.size } : 0, {
            type: "paragraph",
            content,
          })
          // ⚠️ Set the caret here, not with `focus("start")`. That command
          // skips the set when the old caret was at the same number, and the
          // insert then leaves the caret at the end of the moved text.
          .command(({ tr }) => {
            tr.setSelection(Selection.atStart(tr.doc));
            return true;
          })
          .focus()
          .run();
      },
      openFind,
    };
    editor.storage.kbTitleHandoff.exitTop = () => {
      const field = titleLink.title.current;
      if (!field) return false;
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
      return true;
    };
    return () => {
      titleLink.body.current = null;
      if (!editor.isDestroyed) editor.storage.kbTitleHandoff.exitTop = null;
    };
  }, [editor, editable, titleLink, openFind]);

  // The page chips (`pageChips.ts`). A body with a page link loads the page
  // list once, and the list gives each chip its page emoji. Until then a
  // chip draws the file icon.
  // An edit loads the list at once. ⚠️ Not on the first `@`: a state change
  // there redraws the bubble menu, which re-registers its plugin, and that
  // closes the menu that just opened.
  useEffect(() => {
    if (!editor || spaceList.length === 0) return;
    if (editable || chipRuns(editor.state.doc).length > 0) loadPages();
  }, [editor, editable, spaceList.length, loadPages, body]);
  useEffect(() => {
    if (!editor || editor.isDestroyed || !pages) return;
    const icons = editor.storage.kbPageChips.icons;
    icons.clear();
    for (const p of pages) if (p.icon) icons.set(p.id, p.icon);
    editor.view.dispatch(editor.state.tr.setMeta("kbChips", true));
  }, [editor, pages]);

  // The menus' handles, for the plugins of `KnowledgeMenus`.
  useEffect(() => {
    if (!editor) return;
    editor.storage.knowledgeMenus.setHandles({
      slash: slash.render,
      emoji: () => {
        const r = emojiMenu.render();
        return {
          ...r,
          // With no emoji of that name, the keys stay the editor's own.
          onKeyDown: (props) => {
            const query = props.view.state.doc.textBetween(props.range.from + 1, props.range.to);
            return emojiRows(query).length ? r.onKeyDown(props) : false;
          },
        };
      },
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
      mention: () => {
        const r = mentionMenu.render();
        return {
          ...r,
          // With no page and no date for the words, the keys stay the editor's own.
          onKeyDown: (props) => {
            const query = props.view.state.doc.textBetween(props.range.from + 1, props.range.to);
            return mentionRows(query).length ? r.onKeyDown(props) : false;
          },
        };
      },
      onSpecial: (item, e, range) => {
        if (item.kind === "image") {
          e.chain().focus().deleteRange(range).run();
          setImageAt({ from: range.from, to: range.from });
        } else if (item.kind === "pageLink") {
          // "Link to a page" opens the `@` menu, as Notion's mention does.
          e.chain().focus().deleteRange(range).insertContent("@").run();
        }
      },
    });
  });

  // The read view follows the stored body: a save, a revert, a reload.
  const shown = useRef(body);
  useEffect(() => {
    if (!editor || editable || shown.current === body) return;
    shown.current = body;
    // After this render, never inside it: `setContent` renders the node
    // views at once, and React warns on a `flushSync` inside an effect.
    const doc = parseBody(body);
    queueMicrotask(() => {
      if (!editor.isDestroyed) editor.commands.setContent(doc, { emitUpdate: false });
    });
  }, [editor, editable, body]);

  // The editor names the open menu and its active row, so a screen reader
  // follows the `/` menu and the `[[` picker while the caret stays put.
  const emojiShown = emojiMenu.state ? emojiRows(emojiMenu.state.query) : [];
  const mentionShown = mentionMenu.state ? mentionRows(mentionMenu.state.query) : [];
  const menuId = slash.state
    ? "kb-slash-menu"
    : pageMenu.state
      ? "kb-page-menu"
      : mentionMenu.state && mentionShown.length
        ? "kb-mention-menu"
        : emojiMenu.state && emojiShown.length
          ? "kb-emoji-menu"
          : null;
  const menuActive = slash.state
    ? slash.state.active
    : pageMenu.state
      ? pageMenu.state.active
      : mentionMenu.state && mentionShown.length
        ? mentionMenu.state.active
        : (emojiMenu.state?.active ?? 0);
  useEffect(() => {
    const dom = editor && editable ? (editor.view.dom as HTMLElement) : null;
    if (!dom) return;
    if (menuId) {
      dom.setAttribute("aria-haspopup", "listbox");
      dom.setAttribute("aria-expanded", "true");
      dom.setAttribute("aria-controls", menuId);
      dom.setAttribute("aria-activedescendant", optionId(menuId, menuActive));
    } else {
      dom.setAttribute("aria-expanded", "false");
      dom.removeAttribute("aria-controls");
      dom.removeAttribute("aria-activedescendant");
    }
  }, [editor, editable, menuId, menuActive]);

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
  const mentionAnchor = mentionMenu.state?.rect
    ? { getBoundingClientRect: () => mentionMenu.state!.rect!() ?? new DOMRect() }
    : null;
  const emojiAnchor = emojiMenu.state?.rect
    ? { getBoundingClientRect: () => emojiMenu.state!.rect!() ?? new DOMRect() }
    : null;

  return (
    <div ref={shellRef} className={editable ? "kb-editor-shell is-editing" : "kb-editor-shell"} onClick={onClick}>
      {editor && findOpen ? (
        <FindBar editor={editor} editable={editable} openCount={findOpen} onClose={closeFind} scroller={scroller} />
      ) : null}
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
          <TableHandles editor={editor} container={shell} />
          <TableCellTools editor={editor} scrollTarget={scroller} />
          <OptionList
            id="kb-slash-menu"
            label="Blocks"
            anchor={slashAnchor}
            open={Boolean(slash.state)}
            // Headings show with no words typed. Once a member filters, the
            // rows run by score across the groups, and a heading would repeat.
            rows={slashRows.map((b) => ({
              id: b.id,
              label: b.label,
              icon: b.icon,
              description: b.description,
              hint: b.shortcut,
              section: slash.state?.query.trim() ? undefined : b.group,
            }))}
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
          {/* An `@` with no page and no date for the words shows nothing. */}
          <OptionList
            id="kb-mention-menu"
            label="Mention a page or a date"
            anchor={mentionAnchor}
            open={Boolean(mentionMenu.state) && mentionShown.length > 0}
            rows={mentionShown.map((m) =>
              m.kind === "page"
                ? {
                    id: m.id,
                    label: m.label,
                    hint: m.hint,
                    icon: "FileText",
                    glyph: m.glyph ?? undefined,
                    section: m.section,
                  }
                : { id: m.id, label: m.label, hint: m.hint, icon: "Calendar", section: m.section },
            )}
            active={mentionMenu.state?.active ?? 0}
            onPick={(i) => mentionShown[i] && mentionMenu.pick(mentionShown[i])}
            onHover={mentionMenu.setActive}
            emptyText="No page or date has that name."
          />
          {/* A `:` with no emoji of that name shows nothing, so "a :)" stays quiet. */}
          <OptionList
            id="kb-emoji-menu"
            label="Emoji"
            anchor={emojiAnchor}
            open={Boolean(emojiMenu.state) && emojiShown.length > 0}
            rows={emojiShown.map((e) => ({ id: e.name, label: `${e.char}  ${e.name}` }))}
            active={emojiMenu.state?.active ?? 0}
            onPick={(i) => emojiShown[i] && emojiMenu.pick(emojiShown[i])}
            onHover={emojiMenu.setActive}
            emptyText="No emoji has that name."
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
      {editable && (slash.state || pageMenu.state || (mentionMenu.state && mentionShown.length)) ? (
        <span className="sr-only" aria-live="polite">
          {slash.state
            ? (slashRows[slash.state.active]?.label ?? "")
            : pageMenu.state
              ? pagesLoading && !pages
                ? "Loading pages"
                : (pickRows[pageMenu.state.active]?.title ?? "No page has that title.")
              : (mentionShown[mentionMenu.state?.active ?? 0]?.label ?? "")}
        </span>
      ) : null}
    </div>
  );
}
