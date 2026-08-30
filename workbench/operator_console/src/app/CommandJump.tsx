"use client";

// Ctrl-K — jump to any page or any customer (mockup adoption, 2026-08-30).
//
// 🔴 **The pages come from NAV itself** (`lib/palette.ts` derives them), so
// the palette can never offer a page the sidebar dropped. Customers are
// fetched ONCE, on first open, from the same route the roster uses — never
// on every keystroke, and never before somebody asks.
//
// ⚠️ Navigation is a plain location change, exactly like the sidebar's own
// `<a>` links: every page is a server component and a soft transition buys
// nothing here.

import { useCallback, useEffect, useRef, useState } from "react";

import { filterJump, orgItems, pageItems, type JumpItem } from "@/lib/palette";
import { NAV } from "./Header";

export default function CommandJump() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [orgs, setOrgs] = useState<JumpItem[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const openPalette = useCallback(() => {
    setQ("");
    setSel(0);
    setOpen(true);
    if (orgs === null) {
      void (async () => {
        try {
          const res = await fetch("/api/operator/orgs");
          if (!res.ok) {
            setOrgs([]);
            return;
          }
          const body = (await res.json()) as {
            organizations?: { name?: unknown; slug?: unknown }[];
          };
          setOrgs(orgItems(body.organizations ?? []));
        } catch {
          // No list is a state, not an error: pages still jump.
          setOrgs([]);
        }
      })();
    }
  }, [orgs]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open) setOpen(false);
        else openPalette();
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openPalette]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const shown = filterJump([...pageItems(NAV), ...(orgs ?? [])], q);

  function go(item: JumpItem | undefined) {
    if (!item) return;
    setOpen(false);
    window.location.assign(item.href);
  }

  return (
    <>
      <button type="button" className="cmdk" onClick={openPalette}>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <span>Jump…</span>
        <kbd>Ctrl K</kbd>
      </button>

      {open && (
        <div
          className="palette-backdrop"
          onClick={() => setOpen(false)}
          role="presentation"
        >
          <div
            className="palette"
            role="dialog"
            aria-modal="true"
            aria-label="Jump to"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setSel(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setSel((s) => Math.min(s + 1, shown.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setSel((s) => Math.max(s - 1, 0));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  go(shown[sel]);
                }
              }}
              placeholder="Jump to a page or a customer…"
              aria-label="Jump to a page or a customer"
            />
            <ul role="listbox" aria-label="Destinations">
              {shown.length === 0 && (
                <li className="palette-empty">Nothing matches.</li>
              )}
              {shown.map((item, i) => (
                <li key={item.href} role="option" aria-selected={i === sel}>
                  <button
                    type="button"
                    className={i === sel ? "hit sel" : "hit"}
                    onMouseEnter={() => setSel(i)}
                    onClick={() => go(item)}
                  >
                    <span className="hit-label">{item.label}</span>
                    <span className="hit-hint">{item.hint}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
