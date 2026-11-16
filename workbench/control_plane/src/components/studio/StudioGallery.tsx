"use client";

/**
 * The Gallery block (`ai_studio.md` §14 D19, slice STU-D19a).
 *
 * The records of a source as cards in a grid, each with a cover image. It reuses the card of the
 * product: `TaskCardShell`, `TaskCardTitle`, and `CardValue` of the Board, which draws through
 * `ValueElement`. A status field draws as its chip, with the hue that `statusAccent` gives its
 * Board lane. A card with an id links `openBase + id`, as a Board card does, so the app frame
 * opens the record in its panel. The link covers the whole card.
 *
 * THE LAZY LOAD. One IntersectionObserver watches the cards. A card that comes near the view
 * reports its key once, and only then does `coverSrc` give it an address. The `<img>` also
 * carries `loading="lazy"`. So the first markup holds no image address. A browser with no
 * observer counts each card as seen, and `loading="lazy"` alone holds the load. The rules are in
 * `galleryModel.ts`. Fence: `StudioGallery.test.ts`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import { BoardError, CardValue } from "@/components/studio/StudioBoard";
import EntityPill from "@/components/ui/EntityPill";
import { statusAccent } from "@/lib/statusAccent";
import { displayValue, labelOf } from "@/lib/studio/blocks";
import { recordsOf, rowId } from "@/lib/studio/boardModel";
import { moreLine, type FileAddress, type Row } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import {
  EVERY_CARD, cardKey, coverSrc, markSeen, parseGallery, statusInput, type GalleryConfig, type Seen,
} from "@/lib/studio/galleryModel";

/** The observer starts a load this far before a card reaches the view. */
const AHEAD = "240px 0px";

/** One card field. A status draws as its chip. Each other field draws as on a Board card. */
function GalleryValue({ field, value, status }: { field: FieldSpec; value: unknown; status: boolean }) {
  const input = status ? statusInput(field, value) : null;
  if (!input) return <CardValue field={field} value={value} />;
  return (
    <span title={labelOf(field)} className="inline-flex min-w-0 max-w-full items-center">
      <EntityPill fit kind="status" label={input.name} accent={statusAccent(input)} />
    </span>
  );
}

/** The cover of a card: the image once the card was seen, else the placeholder. Both keep one box. */
function Cover({ src }: { src: string | null }) {
  return (
    <div className="flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" loading="lazy" decoding="async" className="size-full object-scale-down" />
      ) : (
        <span data-gallery-placeholder="" aria-hidden><Icon name="Image" size={20} /></span>
      )}
    </div>
  );
}

/**
 * The grid, from props alone. `seen` holds the keys that the observer reported, and `watch` is
 * the ref of each card. Exported for its test, which draws it with a set of seen keys.
 */
export function GalleryGrid({
  config, rows, seen, address, watch, more = null,
}: {
  config: GalleryConfig;
  rows: readonly Row[];
  seen: Seen;
  address: FileAddress | null;
  watch?: (el: HTMLElement | null) => void;
  more?: string | null;
}) {
  return (
    <div className="space-y-2">
      {more ? <p role="status" className="text-xs text-muted-foreground">{more}</p> : null}
      {rows.length === 0 ? <p className="text-xs text-muted-foreground">No cards</p> : (
        <ul aria-label="Gallery" className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
          {rows.map((row, at) => {
            const key = cardKey(row, at);
            const id = rowId(row);
            const title = config.titleField ? displayValue(config.titleField, row[config.titleField.key]) : "";
            return (
              <li key={key} ref={watch} data-gallery-key={key} className="list-none">
                {/* A gallery tile has no done state and no keyboard cursor, so both are false. */}
                <TaskCardShell ariaLabel={title || "Untitled"} completed={false} atCursor={false} className="h-full">
                  <Cover src={coverSrc(config, row, key, seen, address)} />
                  <TaskCardTitle>
                    {id !== null && config.openBase ? (
                      // A record link (STU-AF5). The app frame opens it in the record panel.
                      <a href={`${config.openBase}${encodeURIComponent(id)}`}
                        className="rounded-sm text-foreground outline-none after:absolute after:inset-0 after:rounded-lg hover:underline focus-visible:ring-2 focus-visible:ring-ring">
                        {title || "Untitled"}
                      </a>
                    ) : (title || "Untitled")}
                  </TaskCardTitle>
                  {config.cardFields.length > 0 ? (
                    <div className="flex flex-col gap-1">
                      {config.cardFields.map((f) => (
                        <GalleryValue key={f.key} field={f} value={row[f.key]} status={f === config.statusField} />
                      ))}
                    </div>
                  ) : null}
                </TaskCardShell>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * One IntersectionObserver for the grid. `watch` is the ref of each card. A card reports its key
 * once, when it comes near the view, and the observer lets it go. With no observer, the guard of
 * `lib/shell/FeedCard.tsx` applies, and every card counts as seen.
 */
function useSeenCards(): { seen: Seen; watch: (el: HTMLElement | null) => (() => void) | undefined } {
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set());
  const [every, setEvery] = useState(false);
  const io = useRef<IntersectionObserver | null>(null);
  const cards = useRef(new Set<HTMLElement>());
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") {
      setEvery(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      const keys: string[] = [];
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const key = (e.target as HTMLElement).dataset.galleryKey;
        if (key) keys.push(key);
        observer.unobserve(e.target);
      }
      if (keys.length > 0) setSeen((s) => markSeen(s, keys));
    }, { rootMargin: AHEAD });
    io.current = observer;
    for (const el of cards.current) observer.observe(el);
    return () => {
      observer.disconnect();
      io.current = null;
    };
  }, []);
  // A ref with a cleanup (React 19): a card that unmounts leaves the observer.
  const watch = useCallback((el: HTMLElement | null) => {
    if (!el) return undefined;
    cards.current.add(el);
    io.current?.observe(el);
    return () => {
      cards.current.delete(el);
      io.current?.unobserve(el);
    };
  }, []);
  return { seen: every ? EVERY_CARD : seen, watch };
}

function GalleryView({ config, rows: given, address }: { config: GalleryConfig; rows: Row[]; address: FileAddress | null }) {
  const more = moreLine(given);
  // Records only, once, for every use below.
  const rows = useMemo(() => recordsOf(given) as Row[], [given]);
  const { seen, watch } = useSeenCards();
  return <GalleryGrid config={config} rows={rows} seen={seen} address={address} watch={watch} more={more} />;
}

export function StudioGallery({ data, rows, address }: { data: unknown; rows: Row[]; address: FileAddress | null }) {
  const parsed = useMemo(() => parseGallery(data), [data]);
  if (!parsed.ok) return <BoardError reason={parsed.reason} />;
  return <GalleryView config={parsed.config} rows={rows} address={address} />;
}

export default StudioGallery;
