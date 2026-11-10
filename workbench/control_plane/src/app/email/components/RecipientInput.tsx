"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { suggestContacts, type ContactSuggestion } from "../lib/api";

/**
 * RecipientInput — a To/Cc/Bcc text input with contact autocomplete.
 *
 * Drop-in replacement for the composers' plain comma-separated inputs: same
 * controlled value/onChange contract, plus a suggestion dropdown fed by
 * /email/contacts/suggest (people you've written to, the learned contacts
 * directory, known senders). Autocomplete works on the TOKEN AT THE CARET, so
 * editing the middle of a recipient list suggests for that address only, and
 * already-entered addresses are never re-offered.
 *
 * Keyboard: ↑/↓ move, Enter/Tab accept, Esc closes. Picking inserts the plain
 * address (the send paths parse bare comma-separated emails).
 */

const MIN_CHARS = 2;
const DEBOUNCE_MS = 200;

/** Bounds of the comma-separated token the caret sits in. */
function tokenBounds(value: string, caret: number): { start: number; end: number } {
  const start = value.lastIndexOf(",", Math.max(0, caret - 1)) + 1;
  let end = value.indexOf(",", caret);
  if (end === -1) end = value.length;
  return { start, end };
}

export function RecipientInput({
  value,
  onChange,
  accountId,
  placeholder,
  className,
  wrapperClassName = "relative flex-1 min-w-0",
  ariaLabel,
  readOnly = false,
}: {
  value: string;
  onChange: (v: string) => void;
  accountId?: string | null;
  placeholder?: string;
  /** Classes for the <input> itself (match the surrounding composer). */
  className?: string;
  /** Classes for the positioning wrapper (defaults to a flex-1 cell). */
  wrapperClassName?: string;
  ariaLabel?: string;
  /** No edit, and no suggestion list: a forward in flight freezes its fields. */
  readOnly?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<ContactSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  // The query the current dropdown answers, so stale fetches never reopen it.
  const seq = useRef(0);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = useCallback(() => {
    setOpen(false);
    setItems([]);
    setHi(0);
  }, []);

  /** Look up suggestions for the token at the caret (debounced). */
  const lookup = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? el.value.length;
    const { start, end } = tokenBounds(el.value, caret);
    const q = el.value.slice(start, end).trim();
    if (q.length < MIN_CHARS) {
      close();
      return;
    }
    // Addresses already in the field are done — never re-offer them.
    const taken = new Set(
      el.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean),
    );
    const mySeq = ++seq.current;
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      const found = await suggestContacts(accountId, q).catch(
        () => [] as ContactSuggestion[],
      );
      if (mySeq !== seq.current) return; // a newer keystroke superseded us
      const usable = found.filter(
        (f) => f.email.toLowerCase() !== q.toLowerCase() && !taken.has(f.email.toLowerCase()),
      );
      setItems(usable);
      setHi(0);
      setOpen(usable.length > 0);
    }, DEBOUNCE_MS);
  }, [accountId, close]);

  useEffect(() => () => {
    if (debounce.current) clearTimeout(debounce.current);
  }, []);

  /** Replace the token at the caret with the picked address. */
  const pick = useCallback(
    (s: ContactSuggestion) => {
      const el = inputRef.current;
      if (!el) return;
      const caret = el.selectionStart ?? el.value.length;
      const { start, end } = tokenBounds(el.value, caret);
      const before = el.value.slice(0, start);
      const after = el.value.slice(end);
      const lead = before && !before.endsWith(" ") ? " " : "";
      // Trailing ", " when this was the last token, ready for the next one.
      const insert = `${lead}${s.email}`;
      const next = after.trim()
        ? `${before}${insert}${after}`
        : `${before}${insert}, `;
      onChange(next);
      close();
      requestAnimationFrame(() => {
        const pos = (before + insert).length + (after.trim() ? 0 : 2);
        el.focus();
        el.setSelectionRange(pos, pos);
      });
    },
    [onChange, close],
  );

  // Ties the combobox input to its listbox for a11y.
  const listId = useId();

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((h) => (h + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => (h - 1 + items.length) % items.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      pick(items[hi]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  };

  return (
    <div className={wrapperClassName}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel}
        readOnly={readOnly}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        role="combobox"
        autoComplete="off"
        spellCheck={false}
        className={className}
        onChange={(e) => {
          if (readOnly) return;
          onChange(e.target.value);
          lookup();
        }}
        onClick={lookup}
        onKeyDown={onKeyDown}
        onBlur={() => {
          // Delay so an option's onMouseDown/click can land first.
          setTimeout(close, 120);
        }}
      />
      {open && (
        <div
          id={listId}
          role="listbox"
          className="absolute left-0 top-full mt-1 z-40 w-full min-w-[220px] max-w-[420px] rounded-lg border border-border bg-popover shadow-xl py-1 max-h-56 overflow-y-auto"
        >
          {items.map((s, i) => (
            <button
              key={s.email}
              type="button"
              role="option"
              aria-selected={i === hi}
              // mousedown (not click) so it fires before the input's blur.
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
              onMouseEnter={() => setHi(i)}
              className={`flex w-full flex-col items-start gap-0 px-2.5 py-1.5 text-left ${
                i === hi ? "bg-secondary" : ""
              }`}
            >
              {s.name && (
                <span className="w-full truncate text-xs text-foreground">
                  {s.name}
                </span>
              )}
              <span
                className={`w-full truncate ${
                  s.name
                    ? "text-[10px] text-muted-foreground"
                    : "text-xs text-foreground"
                }`}
              >
                {s.email}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
