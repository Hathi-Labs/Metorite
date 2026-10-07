"use client";

/**
 * A job opens its form from any page (`navigation_shell.md` §6.2, NS-1).
 *
 * A job in the command bar is a link, `/email?do=compose`. The app that owns
 * it renders `<ShellJob id="compose" onOpen={openCompose} />`. On arrival, it
 * runs the opener once and takes `do` out of the address, so a reload or a
 * Back does not open the form a second time.
 *
 * ⚠️ A component with its own `<Suspense>`, not a bare hook. Next refuses to
 * build a static page that reads the query outside a Suspense boundary, and
 * My Tasks has none. The boundary here keeps every caller safe.
 *
 * ⚠️ The opener runs when the app is READY to open it, not on first paint.
 * Pass `ready: false` while the app still loads what the form needs (Email's
 * mailboxes, for one), and the hook waits.
 *
 * NS-4b's filled jobs use the same door: their fields travel as `fill.<name>`
 * parameters, and `jobFields()` reads them. A filled field is a suggestion,
 * never an act (§6.4 rule 2). The form shows it, and the member saves.
 *
 * Fence: `doJob.test.ts`.
 */
import { Suspense, useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { shellBarOn } from "./registry";

/** The `fill.<name>` parameters of a job link, as a plain record. */
export function jobFields(params: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of params) {
    if (k.startsWith("fill.") && k.length > 5) out[k.slice(5)] = v.slice(0, 2000);
  }
  return out;
}

/** The address with `do` and every `fill.` parameter taken out. */
export function withoutJob(pathname: string, params: URLSearchParams): string {
  const next = new URLSearchParams();
  for (const [k, v] of params) {
    if (k !== "do" && !k.startsWith("fill.")) next.append(k, v);
  }
  const q = next.toString();
  return q ? `${pathname}?${q}` : pathname;
}

export interface ShellJobProps {
  id: string;
  onOpen: (fields: Record<string, string>) => void;
  /** False while the app still loads what the form needs. */
  ready?: boolean;
}

export function ShellJob(props: ShellJobProps) {
  // Flag off: no job links, so a crafted `?do=` link opens nothing.
  if (!shellBarOn()) return null;
  return (
    <Suspense fallback={null}>
      <Listen {...props} />
    </Suspense>
  );
}

function Listen({ id, onOpen: open, ready = true }: ShellJobProps): null {
  const params = useSearchParams();
  const pathname = usePathname();
  const asked = params?.get("do") === id;
  // The opener can change on every render. The effect reads the newest.
  const openRef = useRef(open);
  openRef.current = open;
  // ⚠️ Once per link. React runs an effect twice in development, and the
  // replace below lands a render later, so without this the form opened twice.
  const doneFor = useRef<string | null>(null);
  useEffect(() => {
    // ⚠️ Forget the last link once it has left the address. Next keeps the
    // page mounted when only the query changes, so the SAME link a second
    // time ("New task", close, "New task") has the same string, and it was
    // skipped (review, 2026-10-08).
    if (!asked) {
      doneFor.current = null;
      return;
    }
    if (!ready || !params) return;
    const link = params.toString();
    if (doneFor.current === link) return;
    doneFor.current = link;
    const fields = jobFields(new URLSearchParams(params.toString()));
    // The browser's own history, not a router replace: the router asks the
    // server to render the page again, which a job does not need.
    window.history.replaceState(null, "", withoutJob(pathname ?? "/", new URLSearchParams(params.toString())));
    openRef.current(fields);
  }, [asked, ready, params, pathname]);
  return null;
}
