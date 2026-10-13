"use client";

/**
 * The command bar (`navigation_shell.md` §6, NS-1): one box that finds, goes,
 * does and asks, for the whole product.
 *
 * What it shows, in this order (§6.2), each group only when it has a row:
 *   • **Do** — jobs, such as "New task".
 *   • **Go to** — the apps this member holds.
 *   • **Find** — records from each app the member holds: tasks, emails and
 *     people (NS-4a, `GET /api/shell/search`). They arrive a moment later,
 *     under the Do and Go to results, which never move. The two hand-off
 *     rows below them move down, and the highlight follows its row (§6.3).
 *   • **In this page** — "Show all in your Inbox", which hands the words to the
 *     page's own filter (§6.7 rule 3). Only when the page has a filter.
 *   • **Ask** — the coordinator first (NS-4b, `POST /api/shell/intent`): after
 *     a pause on a sentence, one row names the job the words ask for, with the
 *     fields it filled ("Write an email · to Priya · March invoice"). Then
 *     "Ask the assistant", which opens the assistant with the words typed.
 *     Out of credits, the coordinator's row says so in one line (§6.5).
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
import Button from "@/components/ui/Button";
import { openAssistant } from "./assistant";
import { shellChatOn } from "./chatFlag";
import { fillPageFilter, pageFilterName } from "./pageFilter";
import { buildItems, rank, readRecent, rememberRecent, type BarItem } from "./registry";

interface FindItem {
  kind: string;
  title: string;
  hint: string;
  href: string;
}
interface FindGroup {
  app: string;
  label: string;
  items: FindItem[];
}

/**
 * The icon of each kind of Find row. The CRM's four (WS-53 CRM-X2) are the
 * icons of their rows in the CRM rail (`app/crm/lib/views.ts`).
 */
export const FIND_ICONS: Record<string, string> = {
  task: "CircleCheck",
  email: "Mail",
  person: "User",
  lead: "Target",
  deal: "Handshake",
  contact: "Contact",
  company: "Building2",
};
/** Tier 2 waits for a real pause on a sentence (§6.3: two words, 900 ms). */
const INTENT_PAUSE_MS = 900;

type Intent =
  | { kind: "job"; job: string; label: string; href: string; filled: Record<string, string> }
  | { kind: "handoff"; href: string }
  | { kind: "paused"; message: string }
  | { kind: "off" | "none" | "unavailable" };

/** "Write an email · to Priya · March invoice": the job, then what was filled. */
export function intentLabel(label: string, filled: Record<string, string>): string {
  const parts = [label];
  if (filled.to) parts.push(`to ${filled.to}`);
  for (const [k, v] of Object.entries(filled)) if (k !== "to" && v) parts.push(v);
  return parts.join(" · ");
}
/** Wait this long after the last key before asking the apps. */
const FIND_DEBOUNCE_MS = 220;

interface Row {
  key: string;
  group: "Do" | "Go to" | "Find" | "In this page" | "Ask";
  label: string;
  hint: string;
  icon: string;
  run: () => void;
}

/** Rows in their groups, in order, each with its index in the flat list. */
function groupsOf(rows: Row[]): { group: Row["group"]; items: { row: Row; index: number }[] }[] {
  const out: { group: Row["group"]; items: { row: Row; index: number }[] }[] = [];
  rows.forEach((row, index) => {
    const last = out[out.length - 1];
    if (last && last.group === row.group) last.items.push({ row, index });
    else out.push({ group: row.group, items: [{ row, index }] });
  });
  return out;
}

/** Ask only with real words: two or more, or one of four letters or more. */
export function worthAsking(query: string): boolean {
  const words = query.trim().split(/\s+/).filter(Boolean);
  return words.length >= 2 || (words.length === 1 && words[0].length >= 4);
}


/** One row of results: the words, the hint, the icon, and what it does. */
export type ResultRow = Row;

/**
 * THE result engine of the one bar (D100): the rows for some words, from the
 * registry, the record search and the coordinator. Both surfaces read it: the
 * one field in the middle of the shell bar (`AskField` in `ChatBar.tsx`), and
 * this file's modal,
 * which the phone and the flag-off desktop still open. One engine, so the two
 * can never answer the same words two ways.
 *
 * `active` is whether the surface shows results now. `onDone` runs before a
 * row moves the member anywhere. `askRow` adds "Ask the assistant: …". The
 * one field leaves it out, because its Enter IS the ask.
 */
export function useBarResults({
  active,
  query,
  token,
  here,
  panes,
  email,
  jobOrder,
  onDone,
  askRow = true,
}: {
  active: boolean;
  query: string;
  token: NavPane | null;
  here: NavPane | null;
  panes: NavPane[];
  email: string | null;
  jobOrder?: readonly string[];
  onDone: () => void;
  askRow?: boolean;
}): { rows: Row[]; finding: boolean } {
  const router = useRouter();
  const [recent, setRecent] = useState<string[]>([]);
  const [filterName, setFilterName] = useState<string | null>(null);
  const [found, setFound] = useState<{ query: string; groups: FindGroup[] }>({ query: "", groups: [] });
  const [finding, setFinding] = useState(false);
  const [intent, setIntent] = useState<{ query: string; answer: Intent } | null>(null);

  // What the surface needs each time it starts showing results: the member's
  // recent picks, and whether this page has a filter of its own.
  const wasActive = useRef(false);
  useEffect(() => {
    if (active && !wasActive.current) {
      setRecent(readRecent(email));
      setFilterName(pageFilterName());
    }
    wasActive.current = active;
  }, [active, email]);

  const items = useMemo(() => buildItems(panes), [panes]);

  // Tier 1: ask the apps, once the member pauses. A late answer for words the
  // member has since changed is dropped, never shown.
  const scope = token?.href ?? "";
  useEffect(() => {
    const words = query.trim();
    if (!active || words.length < 2) {
      setFound({ query: "", groups: [] });
      setFinding(false);
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      setFinding(true);
      try {
        const params = new URLSearchParams({ q: words });
        if (scope) params.set("scope", scope);
        const res = await fetch(`/api/shell/search?${params}`, { signal: ctrl.signal, cache: "no-store" });
        const body = res.ok ? ((await res.json()) as { groups?: FindGroup[] }) : {};
        if (!ctrl.signal.aborted) setFound({ query: words, groups: body.groups ?? [] });
      } catch {
        /* aborted, or the gateway is away: the other groups still answer */
      } finally {
        if (!ctrl.signal.aborted) setFinding(false);
      }
    }, FIND_DEBOUNCE_MS);
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [active, query, scope]);

  // Tier 2, the coordinator: only on a sentence, only after a real pause.
  useEffect(() => {
    const words = query.trim();
    if (!active || words.split(/\s+/).length < 2) {
      setIntent(null);
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await fetch("/api/shell/intent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ q: words, scope: scope || null }),
          signal: ctrl.signal,
        });
        const answer = (res.ok ? await res.json() : { kind: "unavailable" }) as Intent;
        if (!ctrl.signal.aborted) setIntent({ query: words, answer });
      } catch {
        /* aborted, or away: the other groups still answer */
      }
    }, INTENT_PAUSE_MS);
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [active, query, scope]);

  const rows: Row[] = useMemo(() => {
    const go = (item: BarItem) => () => {
      rememberRecent(email, item.key);
      onDone();
      // ⚠️ On the SAME page, change the address directly. A router push of a
      // new query asks the server to render the page again first, and the job
      // opened seconds late, or after the test gave up. Next follows the
      // browser's own history, so `useSearchParams` still sees the job.
      const url = new URL(item.href, window.location.origin);
      if (url.pathname === window.location.pathname) {
        window.history.pushState(null, "", `${url.pathname}${url.search}`);
      } else {
        router.push(item.href);
      }
    };
    const ranked = rank({ items, query, context: token?.href ?? null, recent, order: jobOrder });
    const out: Row[] = [];
    for (const item of ranked.filter((i) => i.group === "do").slice(0, 5)) {
      out.push({ key: item.key, group: "Do", label: item.label, hint: item.hint, icon: item.icon, run: go(item) });
    }
    for (const item of ranked.filter((i) => i.group === "go").slice(0, 5)) {
      out.push({ key: item.key, group: "Go to", label: item.label, hint: item.hint, icon: item.icon, run: go(item) });
    }
    const words = query.trim();
    if (found.query === words) {
      for (const group of found.groups) {
        for (const [n, hit] of group.items.entries()) {
          out.push({
            key: `find:${group.app}:${n}:${hit.href}`,
            group: "Find",
            label: hit.title,
            hint: hit.hint,
            icon: FIND_ICONS[hit.kind] ?? "Search",
            run: () => {
              onDone();
              // The apps read their deep link when the page loads. On the
              // same page that load must be a real one, or the link is ignored.
              const url = new URL(hit.href, window.location.origin);
              if (url.pathname === window.location.pathname) window.location.assign(hit.href);
              else router.push(hit.href);
            },
          });
        }
      }
    }
    if (words && filterName && token && token.href === here?.href) {
      out.push({
        key: "filter",
        group: "In this page",
        label: `Show all in ${filterName}`,
        hint: `Filter ${filterName} by “${words}”`,
        icon: "ListFilter",
        run: () => {
          onDone();
          void fillPageFilter(words);
        },
      });
    }
    const coordinator = intent?.query === words ? intent.answer : null;
    if (coordinator?.kind === "job") {
      out.push({
        key: `intent:${coordinator.job}`,
        group: "Ask",
        label: intentLabel(coordinator.label, coordinator.filled),
        hint: Object.keys(coordinator.filled).length
          ? "Suggested by AI. The form opens filled in, and you check it before anything is saved or sent."
          : "Suggested by AI. It opens the form for you.",
        icon: "Sparkles",
        run: () => {
          onDone();
          const url = new URL(coordinator.href, window.location.origin);
          if (url.pathname === window.location.pathname) {
            window.history.pushState(null, "", `${url.pathname}${url.search}`);
          } else {
            router.push(coordinator.href);
          }
        },
      });
    } else if (coordinator?.kind === "paused") {
      out.push({
        key: "intent:paused",
        group: "Ask",
        label: coordinator.message,
        hint: "Search and the other results keep working.",
        icon: "CirclePause",
        run: () => undefined,
      });
    }
    if (askRow && worthAsking(words)) {
      out.push({
        key: "ask",
        group: "Ask",
        label: `Ask the assistant: “${words}”`,
        hint: "Opens the assistant with your question typed in. You check it and send it.",
        icon: "Sparkles",
        run: () => {
          onDone();
          // D99: the one assistant opens where the member is, so the page
          // and its context stay. Off, the words go to Chat as before.
          if (shellChatOn()) openAssistant({ draft: words });
          else router.push(`/chat?q=${encodeURIComponent(words)}`);
        },
      });
    }
    return out;
  }, [items, query, token, recent, jobOrder, filterName, here, email, onDone, router, found, intent, askRow]);

  return { rows, finding };
}

/** A stable DOM id for one row, under a surface's own prefix. */
export function resultOptionId(prefix: string, key: string): string {
  return `${prefix}-${key.replace(/[^a-z0-9]/gi, "-")}`;
}

/**
 * The rows, in their named groups, as one listbox. Both surfaces draw it.
 * `active` is the highlighted row's index, or -1 for none.
 */
export function ResultList({
  rows,
  active,
  onHover,
  prefix,
  query,
  finding,
  className = "",
}: {
  rows: readonly Row[];
  active: number;
  onHover: (key: string) => void;
  prefix: string;
  query: string;
  finding: boolean;
  className?: string;
}) {
  return (
    <div id={`${prefix}-list`} role="listbox" aria-label="Results" className={`overflow-y-auto py-1 ${className}`}>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          {finding
            ? "Looking in your apps…"
            : `Nothing matches “${query.trim()}”. Try other words, or one word like “email”.`}
        </p>
      ) : (
        // One `group` per kind, named, so a screen reader announces "Do",
        // "Go to" and so on, and the listbox holds only options and groups.
        groupsOf([...rows]).map(({ group, items }) => (
          <div key={group} role="group" aria-labelledby={`${prefix}-g-${group.replace(/\s+/g, "-")}`}>
            <div
              id={`${prefix}-g-${group.replace(/\s+/g, "-")}`}
              className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
            >
              {group === "Do" && !query.trim() ? "Suggested" : group}
            </div>
            {items.map(({ row, index: i }) => {
              const on = i === active;
              return (
                <div
                  key={row.key}
                  id={resultOptionId(prefix, row.key)}
                  role="option"
                  aria-selected={on}
                  onMouseMove={() => onHover(row.key)}
                  // Keep the cursor in the field: a press here would blur it,
                  // and the bar at the foot of the page closes on blur.
                  onMouseDown={(e) => e.preventDefault()}
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
              );
            })}
          </div>
        ))
      )}
    </div>
  );
}

/**
 * The modal form of the one bar. With the chat bar on, a desktop never opens
 * it: the bar at the foot of the page answers the same words (D100). The
 * phone, and a desktop with the chat bar off, still do.
 */
export function CommandBar({
  open,
  seed,
  onClose,
  panes,
  here,
  email,
  jobOrder,
}: {
  open: boolean;
  seed: string;
  onClose: () => void;
  panes: NavPane[];
  here: NavPane | null;
  email: string | null;
  /** The member's preset job order, for the empty bar (NS-7). */
  jobOrder?: readonly string[];
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(seed);
  const [token, setToken] = useState<NavPane | null>(here);
  // ⚠️ The highlighted row by its KEY, not its place. Find rows arrive a
  // moment later and push the rows below them down. A highlight held by
  // index then sat on a different row, and Enter ran the wrong one.
  const [activeKey, setActiveKey] = useState<string | null>(null);

  // A fresh start on every open: the words it was opened with, and the token
  // of the app the member is in.
  // ⚠️ Only at the moment it OPENS. It reset whenever `here` changed too, and
  // `here` changes when access finishes loading after the bar opened, which
  // wiped what the member had already typed (a 1-in-5 flake that was a real
  // bug on a slow load).
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) {
      setQuery(seed);
      setToken(here);
      setActiveKey(null);
    }
    wasOpen.current = open;
  }, [open, seed, here]);

  const { rows, finding } = useBarResults({ active: open, query, token, here, panes, email, jobOrder, onDone: onClose });

  // The highlight: the held row while it exists, else the first row.
  const held = activeKey ? rows.findIndex((r) => r.key === activeKey) : -1;
  const active = held >= 0 ? held : 0;
  const moveTo = (i: number) => setActiveKey(rows[i]?.key ?? null);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (rows.length) moveTo((active + 1) % rows.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (rows.length) moveTo((active - 1 + rows.length) % rows.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      rows[active]?.run();
    } else if (e.key === "Backspace" && !query && token) {
      e.preventDefault();
      setToken(null);
    }
  };

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
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label={`Search everywhere, not only in ${token.label}`}
              title="Search everywhere"
              onClick={() => {
                setToken(null);
                inputRef.current?.focus();
              }}
            />
          </span>
        ) : null}
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveKey(null);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search or ask anything. Try “new task” or “open email”"
          role="combobox"
          aria-expanded={rows.length > 0}
          aria-controls="cmdbar-list"
          aria-activedescendant={rows[active] ? resultOptionId("cmdbar", rows[active].key) : undefined}
          aria-label="Search or ask anything"
          className="h-12 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
        />
        {finding ? (
          <Icon name="Loader2" size={14} className="shrink-0 animate-spin text-muted-foreground" aria-label="Finding" />
        ) : null}
      </div>

      <ResultList
        rows={rows}
        active={active}
        onHover={(key) => {
          if (key !== activeKey) setActiveKey(key);
        }}
        prefix="cmdbar"
        query={query}
        finding={finding}
        className="max-h-[60vh]"
      />

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
