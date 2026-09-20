"use client";

/**
 * Projects · the tag picker in the task panel (WS-27m).
 *
 * Typing filters the project's registry; Enter takes the highlighted
 * suggestion, or creates a tag when nothing matches. **Creating is shown, not
 * silent**: auto-registration is deliberate — a two-step errand is how tagging
 * gets abandoned — but a text box that quietly mints a tag per typo is how a
 * tag set rots, so the moment says "create".
 *
 * A comma is a separator, never part of a tag. The filter parameter is CSV, so
 * a tag containing one could never be filtered by; treating it as a separator
 * here means one can never be stored in the first place.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useState } from "react";

import { type TagRow, addTag, chipClass, registryOf, removeTag, suggest, wouldCreate } from "../lib/tags";

interface Props {
  value: string[];
  registry: TagRow[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
  /**
   * Drop the "Tags" heading and the "None yet." line (WS-27n's bulk bar,
   * 2026-09-20).
   *
   * The panel is a labelled block in a column. The bulk bar is a row of
   * controls where every field is already named by its placeholder, and a
   * heading over each one would double the bar's height for nothing. The
   * BEHAVIOUR is identical either way — this only removes two lines of
   * chrome, which is why it is a flag here and not a second component.
   */
  compact?: boolean;
  placeholder?: string;
  ariaLabel?: string;
}

export function TagPicker({
  value,
  registry,
  disabled = false,
  onChange,
  compact = false,
  placeholder = "Add a tag…",
  ariaLabel = "Add a tag",
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const lookup = registryOf(registry);
  const options = suggest(query, registry, value);
  const creating = wouldCreate(query, lookup);
  const colorOf = (name: string) =>
    registry.find((t) => t.name.toLowerCase() === name.toLowerCase())?.color;

  const commit = (raw: string) => {
    const next = addTag(value, raw, lookup);
    if (next !== value) onChange(next);
    setQuery("");
  };

  return (
    <div>
      {compact ? null : (
        <span className="text-xs text-muted-foreground">Tags</span>
      )}
      <div className={compact ? "flex flex-wrap gap-1 empty:hidden" : "mt-1 flex flex-wrap gap-1"}>
        {value.map((name) => (
          <span
            key={name}
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ${chipClass(colorOf(name))}`}
          >
            {name}
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${name}`}
              onClick={() => onChange(removeTag(value, name))}
              className="opacity-70 hover:opacity-100"
            >
              <Icon name="X" size={10} />
            </button>
          </span>
        ))}
        {value.length === 0 && !compact ? (
          <span className="text-xs text-muted-foreground">None yet.</span>
        ) : null}
      </div>

      <div className={compact && value.length === 0 ? "relative" : "relative mt-1"}>
        <Input
          inputSize="sm"
          disabled={disabled}
          value={query}
          aria-label={ariaLabel}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          // A blur has to outlive the mousedown on a suggestion, or clicking
          // one closes the list before the click lands.
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw.includes(",")) {
              // Pasting "bug, ops, urgent" adds three tags rather than making
              // one impossible-to-filter tag out of the lot.
              for (const part of raw.split(",")) commit(part);
              return;
            }
            setQuery(raw);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(options.length && !creating ? options[0].name : query);
            } else if (e.key === "Escape") {
              setOpen(false);
            } else if (e.key === "Backspace" && query === "" && value.length) {
              // The convention every chip input has: backspace on an empty box
              // takes the last chip off.
              onChange(value.slice(0, -1));
            }
          }}
        />

        {open && (options.length > 0 || creating) ? (
          <ul className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-border bg-card shadow">
            {options.map((option) => (
              <li key={option.id}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => commit(option.name)}
                  className="flex w-full items-center justify-between px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
                >
                  <span
                    className={`rounded-md px-1.5 py-0.5 ${chipClass(option.color)}`}
                  >
                    {option.name}
                  </span>
                  <span className="text-muted-foreground">
                    {option.task_count ?? 0}
                  </span>
                </button>
              </li>
            ))}
            {creating ? (
              <li className="border-t border-border">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="Plus"
                  className="w-full justify-start"
                  onMouseDown={(e: React.MouseEvent) => e.preventDefault()}
                  onClick={() => commit(query)}
                >
                  Create “{query.trim()}”
                </Button>
              </li>
            ) : null}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
