"use client";

// Who am I — the sidebar identity row (mockup adoption, 2026-08-30).
//
// 🔴 **Exists so an operator always knows which name their next write is
// audited under, and which §5 rank judges it.** Before this, the only way
// to learn "am I signed in as me, or on the shared token?" was to make a
// write and read the audit trail afterwards.
//
// ⚠️ **Renders NOTHING for break-glass and for a failed read** — the same
// discipline as `Elevation.tsx`. The shared token names nobody, and a
// made-up name over real audit lines teaches the team to trust it. Every
// judgement is in `lib/whoami.ts`; `whoami.test.ts` is the fence.

import { useEffect, useState } from "react";

import {
  displayName,
  initials,
  readWhoami,
  showable,
  type Whoami,
} from "@/lib/whoami";

export default function Identity() {
  const [who, setWho] = useState<Whoami | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await fetch("/api/operator/session");
        if (!res.ok) return;
        const parsed = readWhoami(await res.json());
        if (live) setWho(parsed);
      } catch {
        // A console that cannot answer gets no row, not a guessed one.
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  if (!showable(who)) return null;

  return (
    <div className="operator" title={who.actor}>
      <span className="avatar" aria-hidden="true">
        {initials(who.actor)}
      </span>
      <span className="who">
        <b>{displayName(who.actor)}</b>
        {who.role && <span>{who.role}</span>}
      </span>
    </div>
  );
}
