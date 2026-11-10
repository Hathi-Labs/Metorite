"use client";

/**
 * The hover preview of a page chip (WS-61 KB-2k2, `knowledge_base.md` §21.7).
 *
 * After 400 ms of hover, or at once on keyboard focus, a card hangs under a
 * page chip. It shows the page icon, the title, the space, the state and the
 * first 200 characters of the text. It reads the page through the one Knowledge
 * cache (`pageKey`), so a page the member opened already costs no request.
 *
 * A page the member cannot open shows one line, `PREVIEW_REFUSED`, and no
 * title. The card closes on Escape and when the pointer or the focus leaves
 * the chip.
 *
 * ⚠️ One listener set on the editor shell, never one per chip. The chips are
 * links the editor redraws, so delegation is the only form that survives it.
 * The title, the space and the text are React text, never HTML.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import Badge from "@/components/ui/Badge";
import { keepInWindow } from "@/components/ui/OptionList";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import { Skeleton } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { pageKey } from "../../lib/api";
import { PREVIEW_DELAY_MS, PREVIEW_EMPTY, PREVIEW_REFUSED, previewText } from "../../lib/editor/chipPreview";
import { stateLabel, stateTone } from "../../lib/labels";
import { readPage } from "../../lib/readPage";
import type { PageDetail, Space } from "../../lib/types";
import { PageGlyph } from "../PageIcon";

interface Target {
  pageId: string;
  el: HTMLElement;
}

/** The chip under an event target inside `shell`, or `null`. */
function chipOf(target: EventTarget | null, shell: HTMLElement): HTMLElement | null {
  const el = (target as HTMLElement | null)?.closest?.("a.kb-page-chip[data-page-id]") as HTMLElement | null;
  return el && shell.contains(el) ? el : null;
}

export default function PageChipPreview({
  shell,
  spaces,
}: {
  shell: HTMLElement | null;
  spaces: readonly Space[];
}) {
  const [target, setTarget] = useState<Target | null>(null);

  useEffect(() => {
    if (!shell) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const cancel = () => {
      if (timer) clearTimeout(timer);
      timer = null;
    };
    const open = (el: HTMLElement, delay: number) => {
      cancel();
      const pageId = el.getAttribute("data-page-id") ?? "";
      if (!pageId) return;
      const show = () => setTarget((prev) => (prev?.el === el ? prev : { pageId, el }));
      if (delay > 0) timer = setTimeout(show, delay);
      else show();
    };
    const close = () => {
      cancel();
      setTarget(null);
    };
    const onOver = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      const el = chipOf(e.target, shell);
      if (el) open(el, PREVIEW_DELAY_MS);
    };
    const onOut = (e: PointerEvent) => {
      const from = chipOf(e.target, shell);
      if (!from) return;
      const to = e.relatedTarget instanceof Node ? e.relatedTarget : null;
      if (to && from.contains(to)) return;
      close();
    };
    const onFocusIn = (e: FocusEvent) => {
      const el = chipOf(e.target, shell);
      if (el) open(el, 0);
    };
    const onFocusOut = (e: FocusEvent) => {
      if (chipOf(e.target, shell)) close();
    };
    shell.addEventListener("pointerover", onOver);
    shell.addEventListener("pointerout", onOut);
    shell.addEventListener("focusin", onFocusIn);
    shell.addEventListener("focusout", onFocusOut);
    return () => {
      cancel();
      shell.removeEventListener("pointerover", onOver);
      shell.removeEventListener("pointerout", onOut);
      shell.removeEventListener("focusin", onFocusIn);
      shell.removeEventListener("focusout", onFocusOut);
    };
  }, [shell]);

  // Escape closes the card. The window hears the key even when the focus is
  // on the chip, and a click or a scroll of the page closes it too.
  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTarget(null);
    };
    const onDown = () => setTarget(null);
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown, true);
    };
  }, [target]);

  const anchor = useMemo(() => {
    const el = target?.el;
    if (!el) return null;
    return keepInWindow({ getBoundingClientRect: () => el.getClientRects()[0] ?? el.getBoundingClientRect() });
  }, [target]);

  return (
    <AnchoredPanel
      anchor={anchor}
      open={Boolean(target)}
      maxHeight={240}
      className="w-72 max-w-[calc(100vw-1rem)] p-3"
      panelProps={{ role: "tooltip", id: "kb-chip-preview" }}
    >
      {target ? <PreviewBody pageId={target.pageId} spaces={spaces} /> : null}
    </AnchoredPanel>
  );
}

function PreviewBody({ pageId, spaces }: { pageId: string; spaces: readonly Space[] }) {
  const read = useCachedResource(pageKey(pageId), () => readPage(pageId));
  const page: PageDetail | null = read.data?.kind === "page" ? read.data.page : null;
  const refused = !page && (read.data?.kind === "not-found" || Boolean(read.error));
  const spaceName = useCallback(
    (id: string) => spaces.find((s) => s.id === id)?.name ?? null,
    [spaces],
  );

  if (refused) {
    return <p className="text-xs text-muted-foreground">{PREVIEW_REFUSED}</p>;
  }
  if (!page) {
    return (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-3 w-full" />
      </div>
    );
  }
  const text = previewText(page.body_md);
  const space = spaceName(page.space_id);
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2">
        <PageGlyph page={page} size="md" className="mt-0.5" />
        <p className="min-w-0 flex-1 break-words text-sm font-medium text-foreground">{page.title}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {space ? <span className="text-[11px] text-muted-foreground">{space}</span> : null}
        <Badge size="xs" tone={stateTone(page.state)}>
          {stateLabel(page.state)}
        </Badge>
      </div>
      <p className="break-words text-xs text-muted-foreground">{text || PREVIEW_EMPTY}</p>
    </div>
  );
}
