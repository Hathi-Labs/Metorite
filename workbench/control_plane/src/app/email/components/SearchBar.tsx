"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useMemo, useRef, useState } from "react";
import { useEmailStore, FOLDER_ALL, foldersInScope } from "../lib/emailStore";
import {
  SearchFilter,
  addFilter,
  filterKey,
  filterLabel,
  parseQuery,
} from "../lib/searchFilters";
import { chipColors } from "../lib/labelColors";
import { folderLabel } from "../lib/utils";
import { EMAIL_CATEGORIES } from "../lib/types";

/**
 * SearchBar — the email app's search surface, centred in the top bar.
 *
 * Outlook's model: the bar states WHERE it is searching (a scope dropdown on the
 * left, defaulting to the open folder) and WHAT it is filtering by (closable
 * pills). It widens on focus, because a search in progress deserves more room
 * than an idle affordance does.
 *
 * The typed grammar (`from:`, `to:`, `tag:`, `is:unread`, `has:attachment`) and
 * the filter menu produce the SAME pills — see lib/searchFilters.
 */
export function SearchBar() {
  const searchQuery = useEmailStore((s) => s.searchQuery);
  const searchFilters = useEmailStore((s) => s.searchFilters);
  const searchScope = useEmailStore((s) => s.searchScope);
  const searchIsSemantic = useEmailStore((s) => s.searchIsSemantic);
  const selectedFolder = useEmailStore((s) => s.selectedFolder);
  // A custom folder belongs to one mailbox, so All inboxes offers none.
  const allFolders = useEmailStore((s) => s.folders);
  const viewAll = useEmailStore((s) => s.viewAll);
  const folders = useMemo(() => foldersInScope(allFolders, viewAll), [allFolders, viewAll]);
  const availableLabels = useEmailStore((s) => s.availableLabels);
  const labelColors = useEmailStore((s) => s.labelColors);
  const setSearchQuery = useEmailStore((s) => s.setSearchQuery);
  const setSearchScope = useEmailStore((s) => s.setSearchScope);
  const setSearchFilters = useEmailStore((s) => s.setSearchFilters);
  const clearSearch = useEmailStore((s) => s.clearSearch);

  const [focused, setFocused] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // The scope shown: the user's explicit pick, else the open folder.
  const scope = searchScope ?? selectedFolder;
  const scopeName = scope === FOLDER_ALL ? "All folders" : folderLabel(scope);
  const active = Boolean(searchQuery.trim()) || searchFilters.length > 0;
  // Expanded while in use — focused, or showing a search the user can still see
  // the pills of. Collapsed it's a modest affordance; expanded it's a workspace.
  const expanded = focused || active || scopeOpen || filterOpen;

  // Ctrl/Cmd+F focuses search (the browser's own find is far less useful here).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const apply = (next: SearchFilter[]) => setSearchFilters(next);

  const removeFilter = (f: SearchFilter) =>
    apply(searchFilters.filter((x) => filterKey(x) !== filterKey(f)));

  // Enter lifts any typed `from:`/`tag:`/… tokens out of the text into pills,
  // leaving the rest as the query — so the bar always shows what it's filtering.
  const commit = () => {
    const { filters, text } = parseQuery(searchQuery, searchFilters);
    if (filters.length !== searchFilters.length || text !== searchQuery) {
      setSearchQuery(text);
      apply(filters);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Backspace" && !searchQuery && searchFilters.length) {
      // Backspace on an empty input pops the last pill — the chip-input idiom.
      e.preventDefault();
      apply(searchFilters.slice(0, -1));
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (active) clearSearch();
      inputRef.current?.blur();
    }
  };

  // Scope options: every folder the sidebar shows, with All pinned first.
  const scopeOptions = useMemo(() => {
    const rest = folders.filter((f) => f.key !== FOLDER_ALL);
    return [{ key: FOLDER_ALL, label: "All folders" }, ...rest.map((f) => ({
      key: f.key,
      label: f.label,
    }))];
  }, [folders]);

  return (
    <div className={`relative transition-all duration-200 ${expanded ? "w-full max-w-2xl" : "w-full max-w-md"}`}>
      <div
        className={`flex items-center gap-1.5 rounded-md border transition-colors ${
          expanded
            ? "bg-background border-primary/50 shadow-sm"
            : "bg-secondary border-transparent hover:border-border"
        }`}
      >
        {/* ── Scope dropdown: says WHERE we're searching ── */}
        <div className="relative flex-shrink-0">
          <Button variant="text" size="none" radius="keep" layout="flex items-center" onClick={() => setScopeOpen((v) => !v)} title="Choose where to search" className="gap-1 pl-2.5 pr-2 py-1.5 rounded-l-md text-[11px] max-w-[140px]">
            <span className="truncate">{scopeName}</span>
            <Icon name="ChevronDown" size={11} className="flex-shrink-0" />
          </Button>
          {scopeOpen && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setScopeOpen(false)} />
              <div
                role="menu"
                aria-label="Search scope"
                className="absolute left-0 top-full mt-1 z-30 bg-popover border border-border rounded-lg shadow-xl py-1 w-48 max-h-72 overflow-y-auto"
              >
                <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  Search in
                </div>
                {scopeOptions.map((o) => (
                  <button
                    key={o.key}
                    onClick={() => {
                      // Picking a scope navigates the sidebar there too, so the
                      // list, the bar and the sidebar all name the same place.
                      // Re-picking the folder you're already in is a no-op.
                      setSearchScope(o.key === selectedFolder ? null : o.key);
                      setScopeOpen(false);
                      inputRef.current?.focus();
                    }}
                    className="flex items-center justify-between w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                  >
                    <span className="truncate">{o.label}</span>
                    {scope === o.key && (
                      <Icon name="Check" size={11} className="text-primary flex-shrink-0" />
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="w-px h-4 bg-border flex-shrink-0" />
        <Icon name="Search" size={13} className="text-muted-foreground flex-shrink-0 ml-0.5" />

        {/* ── Pills + input share a scrollable row ── */}
        <div className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto scrollbar-hide py-1">
          {searchFilters.map((f) => {
            // Tag pills wear their category colour (the same scheme as chips
            // everywhere else); from/to/flag pills stay neutral primary.
            const c = f.kind === "tag" ? chipColors(f.value, labelColors) : null;
            return (
              <span
                key={filterKey(f)}
                style={c ? { backgroundColor: c.bg, color: c.text } : undefined}
                className={`flex items-center gap-1 flex-shrink-0 rounded-full pl-2 pr-1 py-0.5 text-[11px] font-medium max-w-[180px] ${
                  c ? "" : "bg-primary/10 text-primary"
                }`}
              >
                <span className="truncate">{filterLabel(f)}</span>
                <button
                  onClick={() => removeFilter(f)}
                  aria-label={`Remove ${filterLabel(f)} filter`}
                  className={`rounded-full p-0.5 transition-colors flex-shrink-0 ${
                    c ? "hover:bg-black/15" : "hover:bg-primary/20"
                  }`}
                >
                  <Icon name="X" size={9} />
                </button>
              </span>
            );
          })}
          <input
            ref={inputRef}
            type="text"
            value={searchQuery}
            placeholder={searchFilters.length ? "" : `Search ${scopeName}`}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            className="bg-transparent outline-none text-xs flex-1 min-w-[80px] text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {/* Shown only when the server actually applied semantic re-ranking —
            so it never appears when the feature is off. */}
        {active && searchIsSemantic && (
          <span
            title="Results ranked by meaning, not just keywords"
            className="flex-shrink-0 inline-flex items-center gap-0.5 text-[9px] font-medium text-primary bg-primary/10 rounded px-1 py-0.5"
          >
            <Icon name="Sparkles" size={9} /> Smart
          </span>
        )}

        {active && (
          <Button variant="text" size="icon-xs" radius="keep" layout="" onClick={clearSearch} title="Clear search" aria-label="Clear search" className="rounded flex-shrink-0">
            <Icon name="X" size={13} />
          </Button>
        )}

        {/* ── Filter menu: the discoverable half of the typed grammar ── */}
        <div className="relative flex-shrink-0">
          <button
            onClick={() => setFilterOpen((v) => !v)}
            title="Add a filter"
            aria-label="Add a filter"
            className={`p-1.5 mr-1 rounded transition-colors ${
              filterOpen
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary"
            }`}
          >
            <Icon name="SlidersHorizontal" size={13} />
          </button>
          {filterOpen && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setFilterOpen(false)} />
              <FilterMenu
                filters={searchFilters}
                labels={availableLabels}
                labelColors={labelColors}
                onAdd={(f) => apply(addFilter(searchFilters, f))}
                onClose={() => {
                  setFilterOpen(false);
                  inputRef.current?.focus();
                }}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** The filter picker — tags the mail actually carries, sender/recipient address
 *  filters, plus the state toggles. Everything here is also typeable
 *  (`tag:Newsletter`, `from:alice`, `is:unread`); this is the half a user can
 *  find without knowing the grammar. */
function FilterMenu({
  filters,
  labels,
  labelColors,
  onAdd,
  onClose,
}: {
  filters: SearchFilter[];
  labels: string[];
  labelColors: Record<string, string | null>;
  onAdd: (f: SearchFilter) => void;
  onClose: () => void;
}) {
  const [tagQuery, setTagQuery] = useState("");
  const has = (f: SearchFilter) => filters.some((x) => filterKey(x) === filterKey(f));
  const last = (kind: SearchFilter["kind"]) =>
    [...filters].reverse().find((f) => f.kind === kind)?.value ?? "";

  const shown = useMemo(() => {
    const q = tagQuery.trim().toLowerCase();
    const list = q ? labels.filter((l) => l.toLowerCase().includes(q)) : labels;
    return list.slice(0, 40);
  }, [labels, tagQuery]);

  const toggles: { label: string; f: SearchFilter }[] = [
    { label: "Unread", f: { kind: "unread", value: "" } },
    { label: "Read", f: { kind: "read", value: "" } },
    { label: "Starred", f: { kind: "starred", value: "" } },
    { label: "Has attachment", f: { kind: "attachments", value: "" } },
  ];

  return (
    <div
      role="menu"
      aria-label="Filter menu"
      className="absolute right-0 top-full mt-1 z-30 bg-popover border border-border rounded-lg shadow-xl py-1 w-64"
    >
      <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        Filter by
      </div>
      <div className="flex flex-wrap gap-1 px-2 pb-2">
        {toggles.map((t) => (
          <button
            key={t.label}
            onClick={() => onAdd(t.f)}
            className={`rounded-full px-2 py-0.5 text-[11px] border transition-colors ${
              has(t.f)
                ? "bg-primary/15 text-primary border-primary/30"
                : "border-border text-foreground/70 hover:text-foreground hover:bg-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* From / To — the address filters the backend already supports; typing
          `from:`/`to:` does the same, this is the discoverable half. */}
      <div className="border-t border-border pt-1">
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          People
        </div>
        <div className="px-2 pb-2 space-y-1">
          <AddrInput label="From" onAdd={(v) => onAdd({ kind: "from", value: v })} />
          <AddrInput label="To" onAdd={(v) => onAdd({ kind: "to", value: v })} />
        </div>
      </div>

      {/* Date range — the backend already filters on received_after/before; these
          date inputs are the discoverable half (typing `after:2026-07-01` does
          the same). A bare date is inclusive of the whole day (see toSearchParams). */}
      <div className="border-t border-border pt-1">
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Date
        </div>
        <div className="px-2 pb-2 space-y-1">
          <DateInput label="After" value={last("after")}
            onAdd={(v) => onAdd({ kind: "after", value: v })} />
          <DateInput label="Before" value={last("before")}
            onAdd={(v) => onAdd({ kind: "before", value: v })} />
        </div>
      </div>

      {/* Sender category (email_senders.category) + provider importance. */}
      <div className="border-t border-border pt-1">
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Sender category
        </div>
        <div className="flex flex-wrap gap-1 px-2 pb-2">
          {EMAIL_CATEGORIES.map((cat) => {
            const f: SearchFilter = { kind: "sendercat", value: cat };
            return (
              <button
                key={cat}
                onClick={() => onAdd(f)}
                className={`rounded-full px-2 py-0.5 text-[11px] border transition-colors ${
                  has(f)
                    ? "bg-primary/15 text-primary border-primary/30"
                    : "border-border text-foreground/70 hover:text-foreground hover:bg-secondary"
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-border pt-1">
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Importance
        </div>
        <div className="flex flex-wrap gap-1 px-2 pb-2">
          {["High", "Normal", "Low"].map((level) => {
            const f: SearchFilter = { kind: "importance", value: level };
            return (
              <button
                key={level}
                onClick={() => onAdd(f)}
                className={`rounded-full px-2 py-0.5 text-[11px] border transition-colors ${
                  has(f)
                    ? "bg-primary/15 text-primary border-primary/30"
                    : "border-border text-foreground/70 hover:text-foreground hover:bg-secondary"
                }`}
              >
                {level}
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-border pt-1">
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Tags
        </div>
        {labels.length > 8 && (
          <div className="px-2 pb-1">
            <input
              autoFocus
              value={tagQuery}
              onChange={(e) => setTagQuery(e.target.value)}
              placeholder="Find a tag…"
              className="w-full bg-secondary rounded px-2 py-1 text-[11px] outline-none text-foreground placeholder:text-muted-foreground"
            />
          </div>
        )}
        <div className="max-h-52 overflow-y-auto">
          {shown.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              {labels.length === 0
                ? "No tags yet — they appear as rules label your mail."
                : "No matching tags."}
            </div>
          ) : (
            shown.map((name) => {
              const f: SearchFilter = { kind: "tag", value: name };
              // Colour dot = the same category colour used on chips app-wide.
              const dot = chipColors(name, labelColors).bg;
              return (
                <button
                  key={name}
                  onClick={() => onAdd(f)}
                  className="flex items-center justify-between gap-2 w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                      style={{ backgroundColor: dot }}
                    />
                    <span className="truncate">{name}</span>
                  </span>
                  {has(f) && <Icon name="Check" size={11} className="text-primary flex-shrink-0" />}
                </button>
              );
            })
          )}
        </div>
      </div>

      <div className="border-t border-border mt-1 pt-1 px-2">
        <button
          onClick={onClose}
          className="w-full text-center py-1 rounded text-[11px] text-primary hover:bg-secondary transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  );
}

/** Small labelled date input: picking a date adds an After/Before pill at once
 *  (a native date picker has no "submit", so we commit on change). */
function DateInput({
  label,
  value,
  onAdd,
}: {
  label: string;
  value: string;
  onAdd: (value: string) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 bg-secondary rounded px-2 py-1">
      <span className="text-[10px] text-muted-foreground w-10 flex-shrink-0">{label}</span>
      <input
        type="date"
        value={value}
        onChange={(e) => {
          const v = e.target.value.trim();
          if (v) onAdd(v);
        }}
        className="flex-1 min-w-0 bg-transparent outline-none text-[11px] text-foreground [color-scheme:light] dark:[color-scheme:dark]"
      />
    </div>
  );
}

/** Small labelled address input: Enter adds a From/To pill. */
function AddrInput({
  label,
  onAdd,
}: {
  label: string;
  onAdd: (value: string) => void;
}) {
  const [v, setV] = useState("");
  const submit = () => {
    const t = v.trim();
    if (t) {
      onAdd(t);
      setV("");
    }
  };
  return (
    <div className="flex items-center gap-1.5 bg-secondary rounded px-2 py-1">
      <span className="text-[10px] text-muted-foreground w-8 flex-shrink-0">{label}</span>
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="name or email…"
        className="flex-1 min-w-0 bg-transparent outline-none text-[11px] text-foreground placeholder:text-muted-foreground"
      />
    </div>
  );
}
