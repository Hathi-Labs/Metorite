// The frame every page sits in — sidebar, title, and the provenance banner.
//
// 🔴 **The banner is drawn HERE, not by each page.** A page that renders its
// own banner is a page that can forget to, and the one it forgets is the one
// showing sample numbers. `Shell` takes the origin as a required prop, so a
// screen cannot be written without deciding what it is showing.
//
// ⚠️ Still renders `<Header />` inside, exactly where the eight existing pages
// put it. The sidebar is `position: fixed` and `.wrap` carries the matching
// padding — moving it to `layout.tsx` would put navigation on `/login`, where
// nobody can use it.

import type { ReactNode } from "react";

import { type Origin, provenanceBanner } from "@/lib/source";
import Header from "./Header";

export default function Shell({
  title,
  lede,
  origin,
  note,
  actions,
  children,
}: {
  title: string;
  lede?: ReactNode;
  origin: Origin;
  note?: string;
  /** Buttons that belong to the page as a whole, drawn beside the title. */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const banner = provenanceBanner(origin, note);
  return (
    <>
      <Header />
      <main className="wrap">
        <div className="pagehead">
          <div>
            <h1>{title}</h1>
            {lede && <p className="muted">{lede}</p>}
          </div>
          {actions}
        </div>
        {banner && (
          <div className={`banner ${banner.tone}`} role="status">
            {banner.text}
          </div>
        )}
        {children}
      </main>
    </>
  );
}

/** The staff gate is not configured, so nobody can sign in at all.
 *
 * ⚠️ Kept as its own export because five pages repeat it verbatim, and a
 * sixth that paraphrases it would give the same fault two names. */
export function Unconfigured() {
  return (
    <>
      <Header />
      <main className="wrap">
        <div className="banner danger">
          The staff gate is not configured on this deployment, so nobody can
          sign in. Set it server-side and reload.
        </div>
      </main>
    </>
  );
}
