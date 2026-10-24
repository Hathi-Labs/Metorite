"use client";

/**
 * The filter chips under the Search field (WS-61 KB-3d,
 * `knowledge_base.md` §21.7).
 *
 * Space, Creator, Date and Title only, as the Docmost spotlight has them. A
 * chip with a value shows the value and a clear button. The chips hold no
 * state of the search: the view owns the filters, and the address keeps
 * them (`lib/searchFilters.ts`). Each menu hangs from its chip through
 * `ChromePopover`, so nothing clips it.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

import {
  DATE_PRESETS,
  cleanAuthor,
  dateLabel,
  type SearchFilters,
} from "../lib/searchFilters";
import type { Space } from "../lib/types";
import ChromePopover from "./ChromePopover";

type MenuName = "space" | "author" | "date";

export default function SearchFilterBar({
  filters,
  onChange,
  spaces,
  authors,
}: {
  filters: SearchFilters;
  onChange: (next: SearchFilters) => void;
  spaces: readonly Space[];
  /** The authors in the current hits, for the Creator menu. */
  authors: readonly string[];
}) {
  const [menu, setMenu] = useState<MenuName | null>(null);
  const [trigger, setTrigger] = useState<HTMLElement | null>(null);
  const [typed, setTyped] = useState("");

  const spaceName = filters.space ? (spaces.find((s) => s.id === filters.space)?.name ?? "Space") : null;
  const close = () => setMenu(null);
  const set = (patch: Partial<SearchFilters>) => {
    onChange({ ...filters, ...patch });
    close();
  };

  const open = (name: MenuName) => (e: React.MouseEvent<HTMLElement>) => {
    setTrigger(e.currentTarget);
    setMenu((m) => (m === name ? null : name));
  };

  const chip = (
    name: MenuName,
    label: string,
    icon: string,
    value: string | null,
    clear: () => void,
  ) => (
    <span className="inline-flex items-center">
      <Button
        variant="secondary"
        size="sm"
        icon={icon}
        selected={value !== null}
        aria-haspopup="dialog"
        aria-expanded={menu === name}
        aria-label={value ? `${label}: ${value}` : label}
        className={value ? "max-w-48 rounded-r-none" : "max-w-48"}
        onClick={open(name)}
      >
        <span className="truncate">{value ?? label}</span>
      </Button>
      {value ? (
        <Button
          variant="secondary"
          size="icon-sm"
          icon="X"
          selected
          aria-label={`Clear ${label}`}
          title={`Clear ${label}`}
          className="rounded-l-none border-l-0"
          onClick={clear}
        />
      ) : null}
    </span>
  );

  return (
    <div role="group" aria-label="Filters" className="mt-3 flex flex-wrap items-center gap-2">
      {chip("space", "Space", "BookOpen", spaceName, () => set({ space: null }))}
      {chip("author", "Creator", "User", filters.author, () => set({ author: null }))}
      {chip("date", "Date", "Calendar", filters.date ? dateLabel(filters.date) : null, () => set({ date: null }))}
      <Button
        variant="secondary"
        size="sm"
        icon="Type"
        selected={filters.titleOnly}
        onClick={() => set({ titleOnly: !filters.titleOnly })}
      >
        Title only
      </Button>

      <ChromePopover trigger={trigger} open={menu !== null} onClose={close} label={menuLabel(menu)} id="kb-search-filter-menu">
        {menu === "space" ? (
          <ul className="flex flex-col" aria-label="Spaces">
            {spaces.length === 0 ? (
              <li className="px-2 py-1.5 text-xs text-muted-foreground">No space to pick.</li>
            ) : null}
            {spaces.map((s) => (
              <li key={s.id}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start"
                  selected={s.id === filters.space}
                  onClick={() => set({ space: s.id })}
                >
                  <span className="truncate">{s.name}</span>
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        {menu === "author" ? (
          <div className="flex flex-col gap-1">
            {authors.length ? (
              <ul className="flex flex-col" aria-label="Creators in these results">
                {authors.map((email) => (
                  <li key={email}>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start"
                      selected={email === filters.author}
                      onClick={() => set({ author: email })}
                    >
                      <span className="truncate">{email}</span>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-2 py-1 text-xs text-muted-foreground">No creator in these results yet.</p>
            )}
            <Input
              inputSize="sm"
              type="email"
              aria-label="Creator email"
              placeholder="Type an email"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                e.preventDefault();
                const email = cleanAuthor(typed);
                if (email) {
                  setTyped("");
                  set({ author: email });
                }
              }}
            />
          </div>
        ) : null}
        {menu === "date" ? (
          <ul className="flex flex-col" aria-label="Dates">
            {DATE_PRESETS.map((p) => (
              <li key={p.label}>
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start"
                  selected={p.key === filters.date}
                  onClick={() => set({ date: p.key })}
                >
                  {p.label}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </ChromePopover>
    </div>
  );
}

function menuLabel(menu: MenuName | null): string {
  if (menu === "space") return "Filter by space";
  if (menu === "author") return "Filter by creator";
  return "Filter by date";
}
