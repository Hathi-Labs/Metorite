"use client";

/**
 * The icon of a Knowledge page (WS-61 KB-2f, `knowledge_base.md` §21.7).
 *
 * A page icon is one emoji that a writer chooses, or nothing. With none, a
 * page shows its state icon as before (`labels.pageIcon`). The emoji is
 * text, drawn as React text and never as HTML. It is not the icon NAME of a
 * space, which `labels.spaceIcon` draws through `<Icon>`.
 *
 * `PageGlyph` is the one mark of a page in the tree, the search hits, the
 * home lists and the breadcrumbs. `PageIconControl` sits before the title of
 * the page: a writer opens the picker from it, and a reader sees the emoji.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { invalidate } from "@/lib/dataCache";

import { KNOWLEDGE_CACHE, KnowledgeError } from "../lib/api";
import { setPageIcon } from "../lib/homeApi";
import { pageIcon } from "../lib/labels";
import type { PageState } from "../lib/types";
import EmojiPicker from "./EmojiPicker";

/** The size of a mark: a rail row, a list row, or the title. */
export type GlyphSize = "sm" | "md" | "lg";

const EMOJI_SIZE: Record<GlyphSize, string> = {
  sm: "h-3.5 w-3.5 text-sm",
  md: "h-4 w-4 text-base",
  lg: "h-8 w-8 text-2xl",
};

const ICON_SIZE: Record<GlyphSize, number> = { sm: 14, md: 16, lg: 24 };

/** The emoji of a page, trimmed, or `null` for none. Pure. */
export function iconOf(page: { icon?: string | null }): string | null {
  const value = page.icon?.trim();
  return value ? value : null;
}

/** The mark of a page: its emoji, else its state icon. */
export function PageGlyph({
  page,
  size = "sm",
  className = "",
}: {
  page: { icon?: string | null; state?: PageState | string };
  size?: GlyphSize;
  className?: string;
}) {
  const emoji = iconOf(page);
  if (emoji) {
    return (
      <span
        aria-hidden
        data-page-icon=""
        className={`inline-flex shrink-0 select-none items-center justify-center leading-none ${EMOJI_SIZE[size]} ${className}`}
      >
        {emoji}
      </span>
    );
  }
  return (
    <Icon
      name={pageIcon({ state: (page.state ?? "published") as PageState })}
      size={ICON_SIZE[size]}
      className={`shrink-0 ${className}`}
    />
  );
}

/**
 * The icon before the title of a page. A writer opens the picker from it:
 * the emoji itself, large above the title, or "Add icon" when the page has
 * none. With none, the control takes no room: the caller's header is
 * `relative` and the button floats in its top margin. A reader sees the
 * emoji only, and nothing when there is none.
 */
export function PageIconControl({
  page,
  editable,
}: {
  page: { id: string; title: string; icon?: string | null };
  editable: boolean;
}) {
  const toast = useToast();
  const { isMobile } = useViewMode();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const emoji = iconOf(page);

  if (!editable) {
    return emoji ? (
      <span aria-label={`Page icon ${emoji}`} role="img" className="mb-2 inline-flex h-16 shrink-0 items-center text-5xl leading-none">
        {emoji}
      </span>
    ) : null;
  }

  async function save(next: string | null) {
    setOpen(false);
    anchor?.focus();
    if (next === emoji) return;
    setBusy(true);
    try {
      await setPageIcon(page.id, next);
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: `kb-icon:${page.id}`, variant: "error", title: "Could not change the page icon", description: reason });
    } finally {
      setBusy(false);
      invalidate(KNOWLEDGE_CACHE);
    }
  }

  return (
    <>
      {emoji ? (
        <Button
          variant="ghost"
          size="none"
          className="mb-2 h-16 w-16 shrink-0 justify-start rounded px-0 text-5xl leading-none"
          aria-label={`Change the page icon, now ${emoji}`}
          aria-haspopup="dialog"
          aria-expanded={open}
          title="Change icon"
          disabled={busy}
          onClick={(e) => {
            setAnchor(e.currentTarget);
            setOpen((v) => !v);
          }}
        >
          {emoji}
        </Button>
      ) : (
        // A page with no icon reserves no room. The row floats in the top
        // margin of the header and shows on hover or on focus (Notion).
        <div className="absolute left-0 top-1 flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            icon="SmilePlus"
            // A phone has no hover, so "Add icon" shows at all times there.
            className={`${isMobile ? "" : "reveal-on-hover"} shrink-0 text-muted-foreground`}
            aria-haspopup="dialog"
            aria-expanded={open}
            disabled={busy}
            onClick={(e) => {
              setAnchor(e.currentTarget);
              setOpen((v) => !v);
            }}
          >
            Add icon
          </Button>
        </div>
      )}
      <EmojiPicker
        anchor={anchor}
        open={open}
        label="Page icon"
        onPick={(char) => void save(char)}
        onRemove={emoji ? () => void save(null) : undefined}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
