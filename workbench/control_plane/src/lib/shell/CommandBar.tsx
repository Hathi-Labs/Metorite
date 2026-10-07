"use client";

/**
 * The command bar (`navigation_shell.md` §6, NS-1): one box that finds, goes,
 * does and asks, for the whole product.
 *
 * What it shows, in this order (§6.2), each group only when it has a row:
 *   • **Do** — jobs, such as "New task".
 *   • **Go to** — the apps this member holds.
 *   • **In this page** — "Show all in your Inbox", which hands the words to the
 *     page's own filter (§6.7 rule 3). Only when the page has a filter.
 *   • **Ask** — "Ask the assistant", which opens the assistant with the words
 *     already typed. NS-4b makes this row answer in place.
 *
 * The "in Email" token (§6.4 rule 3) ranks the app the member is in first.
 * `Backspace` on empty words takes it off.
 *
 * ⚠️ No `⌘K` handling here. `ShellBar.tsx` holds the one listener, and this
 * file handles only the keys of its own field: arrows, Enter, Escape and
 * Backspace (`seams.test.ts` allows one listener file).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Icon from "@/components/Icon";
import Modal from "@/components/ui/Modal";
import type { NavPane } from "@/lib/nav";
import { FILL_PAGE_FILTER, buildItems, rank, readRecent, rememberRecent, type BarItem } from "./registry";

interface Row {
  key: string;
  group: "Do" | "Go to" | "In this page" | "Ask";
  label: string;
  hint: string;
  icon: string;
  run: () => void;
}

/** Ask only with real words: two or more, or one of four letters or more. */
export function worthAsking(query: string): boolean {
  const words = query.trim().split(/\s+/).filter(Boolean);
  return words.length >= 2 || (words.length === 1 && words[0].length >= 4);
}

export function CommandBar({
  open,
  seed,
  onClose,
  panes,
  here,
  email,
}: {
  open: boolean;
  seed: string;
  onClose: () => void;
  panes: NavPane[];
  here: NavPane | null;
  email: string | null;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(seed);
  const [token, setToken] = useState<NavPane | null>(here);
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const [filterName, setFilterName] = useState<string | null>(null);

  // A fresh start on every open: the words it was opened with, and the token
  // of the app the member is in.
  useEffect(() => {
    if (!open) return;
    setQuery(seed);
    setToken(here);
    setActive(0);
    setRecent(readRecent(email));
    const filter = document.querySelector<HTMLElement>("[data-page-filter]");
    setFilterName(filter && filter.offsetParent !== null ? filter.getAttribute("data-page-filter") || null : null);
  }, [open, seed, here, email]);

  const items = useMemo(() => buildItems(panes), [panes]);

  const rows: Row[] = useMemo(() => {
    const go = (item: BarItem) => () => {
      rememberRecent(email, item.key);
      onClose();
      router.push(item.href);
    };
    const ranked = rank({ items, query, context: token?.href ?? null, recent });
    const out: Row[] = [];
    for (const item of ranked.filter((i) => i.group === "do").slice(0, 5)) {
      out.push({ key: item.key, group: "Do", label: item.label, hint: item.hint, icon: item.icon, run: go(item) });
    }
    for (const item of ranked.filter((i) => i.group === "go").slice(0, 5)) {
      out.push({ key: item.key, group: "Go to", label: item.label, hint: item.hint, icon: item.icon, run: go(item) });
    }
    const words = query.trim();
    if (words && filterName && token && token.href === here?.href) {
      out.push({
        key: "filter",
        group: "In this page",
        label: `Show all in ${filterName}`,
        hint: `Filter ${filterName} by “${words}”`,
        icon: "ListFilter",
        run: () => {
          onClose();
          window.dispatchEvent(new CustomEvent(FILL_PAGE_FILTER, { detail: { query: words } }));
        },
      });
    }
    if (worthAsking(words)) {
      out.push({
        key: "ask",
        group: "Ask",
        label: `Ask the assistant: “${words}”`,
        hint: "Opens the assistant with your question typed in. You check it and send it.",
        icon: "Sparkles",
        run: () => {
          onClose();
          router.push(`/chat?q=${encodeURIComponent(words)}`);
        },
      });
    }
    return out;
  }, [items, query, token, recent, filterName, here, email, onClose, router]);

  // Keep the highlight on a row that exists.
  useEffect(() => {
    if (active >= rows.length) setActive(Math.max(0, rows.length - 1));
  }, [rows.length, active]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (rows.length ? (i + 1) % rows.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (rows.length ? (i - 1 + rows.length) % rows.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      rows[active]?.run();
    } else if (e.key === "Backspace" && !query && token) {
      e.preventDefault();
      setToken(null);
    }
  };

  const optionId = (key: string) => `cmdbar-${key.replace(/[^a-z0-9]/gi, "-")}`;
  let lastGroup: Row["group"] | null = null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      label="Search or ask"
      placement="top"
      size="xl"
      showClose={false}
      initialFocus={inputRef}
      className="overflow-hidden p-0"
    >
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Icon name="Sparkles" size={16} className="shrink-0 text-primary" />
        {token ? (
          <span className="flex shrink-0 items-center gap-1 rounded-md bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
            in {token.label}
            <button
              type="button"
              aria-label={`Search everywhere, not only in ${token.label}`}
              title="Search everywhere"
              onClick={() => {
                setToken(null);
                inputRef.current?.focus();
              }}
              className="rounded hover:text-foreground"
            >
              <Icon name="X" size={11} />
            </button>
          </span>
        ) : null}
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search or ask anything. Try “new task” or “open email”"
          role="combobox"
          aria-expanded={rows.length > 0}
          aria-controls="cmdbar-list"
          aria-activedescendant={rows[active] ? optionId(rows[active].key) : undefined}
          aria-label="Search or ask anything"
          className="h-12 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
        />
      </div>

      <div id="cmdbar-list" role="listbox" aria-label="Results" className="max-h-[60vh] overflow-y-auto py-1">
        {rows.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">
            Nothing matches “{query.trim()}”. Try other words, or one word like “email”.
          </p>
        ) : (
          rows.map((row, i) => {
            const heading = row.group !== lastGroup ? row.group : null;
            lastGroup = row.group;
            const on = i === active;
            return (
              <div key={row.key}>
                {heading ? (
                  <div className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {heading === "Do" && !query.trim() ? "Suggested" : heading}
                  </div>
                ) : null}
                <div
                  id={optionId(row.key)}
                  role="option"
                  aria-selected={on}
                  onMouseMove={() => setActive(i)}
                  onClick={row.run}
                  className={`mx-1 flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 ${
                    on ? "bg-primary/10 text-foreground" : "text-foreground"
                  }`}
                >
                  <Icon name={row.icon} size={16} className={`shrink-0 ${on ? "text-primary" : "text-muted-foreground"}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]">{row.label}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{row.hint}</span>
                  </span>
                  {on ? <Icon name="CornerDownLeft" size={13} className="shrink-0 text-muted-foreground" /> : null}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Keyboard hints. A phone has no arrows and no Esc, so it shows none. */}
      <div className="hidden items-center gap-3 border-t border-border px-4 py-2 text-[11px] text-muted-foreground sm:flex">
        <span>↑ ↓ to move</span>
        <span>Enter to open</span>
        <span>Esc to close</span>
        {token ? <span className="ml-auto">Backspace to search everywhere</span> : null}
      </div>
    </Modal>
  );
}
