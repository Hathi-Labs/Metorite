"use client";

import Icon from "@/components/Icon";
import ScrollStrip from "@/components/ScrollStrip";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { useEffect, useRef, useState } from "react";
import { useEmailStore } from "../lib/emailStore";
import { SearchFilter, addFilter, filterKey } from "../lib/searchFilters";
import { chipColors } from "../lib/labelColors";
import { getMessageFacets, MessageFacets } from "../lib/api";

/**
 * QuickFilters — a one-click chip row above the mail list.
 *
 * These reproduce the old "Rapid Inbox" buckets (Needs reply, Awaiting,
 * Follow-up, Newsletter, Marketing, …) as ordinary search filters, so the same
 * triage lives inside the regular inbox instead of a separate view. Each chip
 * toggles a search pill — the SearchBar shows the same pills, and both read the
 * one store, so they stay in sync. Tag chips wear the app-wide category colour.
 *
 * The row is FACET-DRIVEN: it shows only the filters that have mail behind them
 * in the folder you're looking at. A fixed list offered "Cold Email" in Sent and
 * "Needs reply" in Drafts — filters guaranteed to return nothing — and an empty
 * result from a chip is ambiguous in the worst way: you can't tell "no such mail
 * here" from "this is broken".
 *
 * ONE LINE, and every chip reachable (owner report, 2026-10-06). A busy mailbox
 * has more chips than the row is wide, and the row hid its scrollbar, so a
 * mouse could not reach the chips past the edge. The row is now a shared
 * `ScrollStrip`: a fade and an arrow on each edge with chips behind it, and a
 * vertical wheel that moves it sideways. A phone swipes it. The filter icon at
 * its start opens "All filters", every chip wrapped in one panel, so a member
 * sees the whole set at once on any screen. Both read the one store.
 */

/** Curated triage buckets. Order = most-actionable first. `facet` names the key
 *  in the facets response that decides whether this chip has anything behind
 *  it (lowercased label, or one of the scalar buckets). */
const CHIPS: { label: string; facet: string; f: SearchFilter }[] = [
  { label: "Unread", facet: "unread", f: { kind: "unread", value: "" } },
  // Conversation-status labels the reply pipeline writes to em.categories.
  { label: "Needs reply", facet: "needs reply",
    f: { kind: "tag", value: "Needs Reply" } },
  { label: "Awaiting", facet: "awaiting reply",
    f: { kind: "tag", value: "Awaiting Reply" } },
  { label: "Follow-up", facet: "follow-up",
    f: { kind: "tag", value: "Follow-up" } },
  { label: "FYI", facet: "fyi", f: { kind: "tag", value: "FYI" } },
  { label: "Done", facet: "done", f: { kind: "tag", value: "Done" } },
  // Cleanup categories the rules engine writes to em.categories.
  { label: "Newsletter", facet: "newsletter",
    f: { kind: "tag", value: "Newsletter" } },
  { label: "Marketing", facet: "marketing",
    f: { kind: "tag", value: "Marketing" } },
  { label: "Receipt", facet: "receipt", f: { kind: "tag", value: "Receipt" } },
  { label: "Calendar", facet: "calendar", f: { kind: "tag", value: "Calendar" } },
  { label: "Notification", facet: "notification",
    f: { kind: "tag", value: "Notification" } },
  { label: "Cold Email", facet: "cold email",
    f: { kind: "tag", value: "Cold Email" } },
  // Last, and deliberately not a category: the mail the rules never reached.
  // It's the pile the Email Cleaner exists to drain, surfaced where you read.
  { label: "Uncategorized", facet: "uncategorized",
    f: { kind: "uncategorized", value: "" } },
];

/** Count behind a chip, or 0 when the folder has none. */
function facetCount(facets: MessageFacets | null, key: string): number {
  if (!facets) return 0;
  if (key === "unread") return facets.unread;
  if (key === "uncategorized") return facets.uncategorized;
  return facets.labels?.[key] ?? 0;
}

// Facet keys the curated chips already represent, plus the legacy Reply-Zero
// names ("Reply"/"To Reply" → Needs Reply, "Actioned" → Done) — old-labelled
// mail must light up the modern chip, not spawn a duplicate legacy one.
const CURATED_FACETS = new Set(CHIPS.map((c) => c.facet));
const LEGACY_ALIASES = new Set(["reply", "to reply", "actioned"]);

const titleCase = (s: string) =>
  s.replace(/\b\w/g, (ch) => ch.toUpperCase());

/** Chips for the CUSTOM rule labels present in this folder — every filter the
 *  user's rules write gets a pill, not only the built-in vocabulary. Facet keys
 *  are lowercased; tag matching server-side is case-insensitive, so the
 *  title-cased value filters correctly. Busiest first, capped to keep the row
 *  scannable. */
function extraChips(
  facets: MessageFacets | null,
): { label: string; facet: string; f: SearchFilter }[] {
  if (!facets?.labels) return [];
  return Object.entries(facets.labels)
    .filter(
      ([key, n]) => n > 0 && !CURATED_FACETS.has(key) && !LEGACY_ALIASES.has(key)
    )
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([key]) => ({
      label: titleCase(key),
      facet: key,
      f: { kind: "tag", value: titleCase(key) } as SearchFilter,
    }));
}

export function QuickFilters() {
  // The facets of the view: every mailbox in All inboxes (EM-T8d).
  const accountId = useEmailStore((s) => (s.viewAll ? null : s.selectedAccountId));
  const selectedFolder = useEmailStore((s) => s.selectedFolder);
  const emails = useEmailStore((s) => s.emails);
  const searchFilters = useEmailStore((s) => s.searchFilters);
  const setSearchFilters = useEmailStore((s) => s.setSearchFilters);
  const labelColors = useEmailStore((s) => s.labelColors);
  const [facets, setFacets] = useState<MessageFacets | null>(null);

  // Re-read the facets when the folder changes, and again when the list
  // changes underneath us (labelling, archiving and the cleaner's sweep all
  // move mail between buckets — a stale row would keep offering a chip whose
  // mail has since been filed).
  useEffect(() => {
    let alive = true;
    getMessageFacets(accountId, selectedFolder)
      .then((f) => {
        if (alive) setFacets(f);
      })
      .catch(() => {
        // The chips are an accelerant, not a requirement. On failure we fall
        // back to showing everything rather than an empty row, so the user is
        // never left with fewer tools than before.
        if (alive) setFacets(null);
      });
    return () => {
      alive = false;
    };
  }, [accountId, selectedFolder, emails.length]);

  const isActive = (f: SearchFilter) =>
    searchFilters.some((x) => filterKey(x) === filterKey(f));

  const toggle = (f: SearchFilter) => {
    if (isActive(f)) {
      setSearchFilters(searchFilters.filter((x) => filterKey(x) !== filterKey(f)));
    } else {
      setSearchFilters(addFilter(searchFilters, f));
    }
  };

  // Show a chip when it has mail behind it — or when it's already on, because
  // silently removing the control that produced the current view would strand
  // the user in a filtered list with no visible way back out. Custom rule
  // labels follow the curated chips.
  const visible = [
    ...CHIPS.filter(
      ({ facet, f }) => !facets || facetCount(facets, facet) > 0 || isActive(f)
    ),
    ...extraChips(facets),
  ];

  const activeCount = visible.filter(({ f }) => isActive(f)).length;
  // Changes when a chip turns on or off, from here or from a search pill, so
  // the strip can bring a chip that turned on into view.
  const revealKey = searchFilters.map(filterKey).join("|");

  if (visible.length === 0) return null;

  const chips = visible.map(({ label, facet, f }) => (
    <Chip
      key={label}
      label={label}
      f={f}
      n={facetCount(facets, facet)}
      active={isActive(f)}
      folder={selectedFolder}
      labelColors={labelColors}
      onToggle={toggle}
    />
  ));

  return (
    <div className="flex items-center gap-1 pl-1.5 pr-3 sm:pl-2.5 sm:pr-4 py-1.5 border-b border-border flex-shrink-0 bg-card/40">
      <AllFilters count={visible.length} active={activeCount}>
        {chips}
      </AllFilters>
      <ScrollStrip label="Quick filters" revealKey={revealKey} className="gap-1.5">
        {chips}
      </ScrollStrip>
    </div>
  );
}

/** One chip. The strip and the "All filters" panel draw the same one. */
function Chip({
  label,
  f,
  n,
  active,
  folder,
  labelColors,
  onToggle,
}: {
  label: string;
  f: SearchFilter;
  n: number;
  active: boolean;
  folder: string;
  labelColors: Parameters<typeof chipColors>[1];
  onToggle: (f: SearchFilter) => void;
}) {
  const c = f.kind === "tag" ? chipColors(f.value, labelColors) : null;
  return (
    <button
      onClick={() => onToggle(f)}
      aria-pressed={active}
      title={n > 0 ? `${n} in ${folder}` : undefined}
      style={active && c ? { backgroundColor: c.bg, color: c.text } : undefined}
      className={`flex items-center gap-1.5 flex-shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-medium border transition-colors ${
        active
          ? c
            ? "border-transparent"
            : "bg-primary text-primary-foreground border-transparent"
          : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
      }`}
    >
      {c && !active && (
        <span
          className="w-2 h-2 rounded-full flex-shrink-0"
          style={{ backgroundColor: c.bg }}
        />
      )}
      {label}
      {n > 0 && (
        <span className={active ? "opacity-70" : "text-muted-foreground/70"}>
          {n}
        </span>
      )}
    </button>
  );
}

/**
 * The filter icon at the start of the row, and the panel it opens: every chip
 * at once, wrapped. A badge counts the filters that are on, because one of
 * them can sit past the edge of the row.
 *
 * The panel stays open while the member picks, since filters combine. A click
 * outside, Escape or the icon again closes it.
 */
function AllFilters({
  count,
  active,
  children,
}: {
  count: number;
  active: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // The wrapper is the anchor: `Button` does not forward a ref, and the
  // wrapper is exactly the icon's box.
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (shouldDismiss(e.target as Element | null, domClickWalk(rootRef.current))) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div
      ref={(el) => {
        rootRef.current = el;
        setAnchor(el);
      }}
      className="relative flex-shrink-0"
    >
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`All filters (${count})`}
        title="All filters"
        className="relative text-muted-foreground"
      >
        <Icon name="ListFilter" size={14} />
        {active > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-primary px-0.5 text-[9px] font-semibold leading-none text-primary-foreground">
            {active}
          </span>
        )}
      </Button>
      <AnchoredPanel
        anchor={anchor}
        open={open}
        maxHeight={360}
        className="w-[22rem] max-w-[calc(100vw-1.5rem)] p-3"
        panelProps={{ role: "dialog", "aria-label": "All filters" }}
      >
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          All filters
        </p>
        <div className="flex max-h-[300px] flex-wrap gap-1.5 overflow-y-auto">{children}</div>
      </AnchoredPanel>
    </div>
  );
}
