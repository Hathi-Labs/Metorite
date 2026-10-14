"use client";

/**
 * The top-left of the app: the customer's logo, and nothing of ours beside it.
 *
 * ⚠️ Owner decision, 2026-10-11: our own mark left this lockup. Since
 * 2026-10-14 the bar and the sidebar show no Metorite logo, and the comet
 * signs the foot of the account menu instead (owner, 2026-10-14). A
 * workspace with no logo still shows the comet
 * as its badge (`OurMark`). A customer's logo carries no "powered by" caption
 * here, in the bar or anywhere else. Fence:
 * `orgBrandLockup.mode.test.ts` "no caption beside a customer's logo".
 *
 * There are two shells — the desktop sidebar and the mobile menu — and this
 * component exists so there is one lockup rather than two. The last time a
 * visual element was written twice in this tree the copies drifted, and a brand
 * mark that renders differently depending on window width is worse than no
 * brand mark at all.
 *
 * The fallback is not an afterthought: an organisation that has uploaded
 * nothing gets our own mark, deliberately, and it must look finished rather
 * than like a logo that failed to load. `lockup()` in `lib/orgBranding.ts`
 * owns that decision and is tested for it.
 */

import Link from "next/link";
import { useAccess } from "@/components/AccessProvider";
import { useEffect, useState } from "react";

import { MetoriteMark } from "@/components/MetoriteMark";
import {
  type OrgBranding,
  lockup,
  logoBoxWidth,
  readCachedBranding,
  writeCachedBranding,
} from "@/lib/orgBranding";

// ── One fetch per page load, shared by both shells ─────────────────────────
//
// The desktop sidebar and the mobile menu are mounted at the same time on a
// tablet-width viewport. A naive `useEffect(fetch)` in each would issue two
// identical requests on every navigation; the module-level cache collapses them
// to one, and the in-flight promise collapses the concurrent case that a plain
// value cache still misses.

let cached: OrgBranding | null = null;
let inFlight: Promise<OrgBranding> | null = null;

async function fetchBranding(): Promise<OrgBranding> {
  const res = await fetch("/api/settings/branding", { cache: "no-store" });
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()) as OrgBranding;
}

/** Drop the cache so a fresh upload shows up without a reload. */
export function invalidateOrgBranding(next?: OrgBranding): void {
  cached = next ?? null;
  inFlight = null;
  // Persist immediately: an admin who uploads a logo and reloads must not be
  // shown the old one back from a stale cache.
  writeCachedBranding(storage(), cached);
  for (const listener of listeners) listener(cached);
}

const listeners = new Set<(b: OrgBranding | null) => void>();

/** `undefined` on the server — every storage helper is written to accept that. */
function storage(): Storage | undefined {
  return typeof window === "undefined" ? undefined : window.localStorage;
}

export function useOrgBranding(): OrgBranding | null {
  const [branding, setBranding] = useState<OrgBranding | null>(cached);

  useEffect(() => {
    let alive = true;
    listeners.add(setBranding);

    // Read from storage BEFORE the network (OI-3a). Deliberately inside the
    // effect rather than in a `useState` initialiser: the server-rendered HTML
    // carries the fallback, so seeding initial state from storage would be a
    // hydration mismatch — React would discard the client tree and warn. One
    // frame after hydration is the correct place for a client-only value.
    //
    // ⚠️ EVERY instance seeds itself here, not only the first. Effects run
    // child first, so the first instance to run fills `cached` and an
    // ancestor's instance then found it full and skipped. `ShellFrame`'s is
    // an ancestor, so the bar's right-end comet waited on the network, 2.9 s
    // late for a member with a cached logo (review, 2026-10-11). Storage is
    // read again only while it holds nothing. The fetch below stays one.
    // Fence: `e2e/shell-signature.spec.ts` "a cached logo signs the bar".
    if (cached === null) cached = readCachedBranding(storage());
    if (cached) setBranding(cached);

    // Revalidate regardless of a cache hit — stale-while-revalidate. A logo
    // changed on another device, or removed by another admin, has to land here
    // without the member clearing anything.
    inFlight ??= fetchBranding()
      .then((b) => {
        writeCachedBranding(storage(), b);
        return b;
      })
      .catch(() => {
        // A failed read renders whatever we already had, falling back to our own
        // mark — which is also what an org with no logo gets. There is nothing
        // here for a member to act on, so it is not surfaced as an error state.
        // The cache is NOT cleared: an outage must not blank a customer's brand.
        return cached ?? { logo: null, updatedBy: "", updatedAt: "" };
      });

    void inFlight.then((b) => {
      cached = b;
      if (alive) setBranding(b);
    });

    return () => {
      alive = false;
      listeners.delete(setBranding);
    };
  }, []);

  return branding;
}

interface Props {
  /** Subtitle shown when the org has no logo — "Control Plane", "Home", … */
  fallbackCaption: string;
  /** Where the mark links to. Both shells send it home. */
  href?: string;
  onNavigate?: () => void;
  /** Height of the mark in px. The two shells differ by a hair. */
  height?: number;
  /** Widest the logo may render before it starts crowding the nav controls. */
  maxWidth?: number;
  /** One line, for the shell bar. `BrandMark`'s `compact` says what changes. */
  compact?: boolean;
}

export default function OrgBrandLockup({
  fallbackCaption,
  href = "/",
  onNavigate,
  height = 28,
  maxWidth = 152,
  compact = false,
}: Props) {
  const branding = useOrgBranding();
  // D51: the organization's NAME is the workspace indicator now that hostnames
  // never carry it. Resolved server-side by /auth/me (never request input) and
  // already in the access context every shell renders from.
  const { access } = useAccess();
  const orgName =
    access.organization.display_name?.trim() ||
    access.organization.slug?.trim() ||
    "";

  return (
    <Link href={href} onClick={onNavigate} className="block min-w-0">
      <BrandMark
        branding={branding}
        fallbackCaption={fallbackCaption}
        orgName={orgName}
        height={height}
        maxWidth={maxWidth}
        compact={compact}
      />
    </Link>
  );
}

/**
 * The shell bar's lockup: 24px tall, one line, and 176px wide at most. That is
 * the bar's `w-64` brand zone less the fold column, the gap and the padding.
 * `BarBrand` in `AppShell.tsx` and the two Settings previews read this one
 * value, so a preview cannot drift from what the bar draws.
 */
export const BAR_LOCKUP = { height: 24, maxWidth: 176, compact: true } as const;

/**
 * The mark itself, without the link.
 *
 * Separated so Settings → Organization can preview exactly what the shell will
 * render, from the same code, instead of a copy that looks right on the day it
 * is written. A preview you can click home from is also just a trap.
 */
export function BrandMark({
  branding,
  fallbackCaption,
  orgName = "",
  height = 28,
  maxWidth = 152,
  mode,
  compact = false,
}: {
  branding: OrgBranding | null | undefined;
  fallbackCaption: string;
  /** The organization's display name — the D51 workspace indicator. */
  orgName?: string;
  height?: number;
  /** The widest the logo may be. The logo has the whole width (2026-10-11). */
  maxWidth?: number;
  /** Pin one colour mode, for a preview of it. Absent, the page's mode picks. */
  mode?: "light" | "dark";
  /**
   * One line, for the full-width shell bar (owner, 2026-10-09). A customer's
   * logo alone. Ours is the mark and the organization's name. Nothing stacks,
   * so it fits a 44px bar.
   */
  compact?: boolean;
}) {
  const mark = lockup(branding, fallbackCaption, orgName);

  // ── A customer's logo stands alone (owner, 2026-10-11) ───────────────────
  //
  // It used to carry "powered by Metorite", beside it in the bar and under it
  // in the sidebar's head and the phone menu. The logo has its whole slot
  // now, and nothing of ours signs the shell (owner, 2026-10-14).
  if (mark.kind === "org") {
    // ── One logo, two colour modes (owner request, 2026-10-09) ──────────────
    // The page root carries `light` or `dark` (next-themes, Providers.tsx),
    // and dark is the default. So the light image shows only under `.light`,
    // and the dark one (the white version, or the logo on a light card) shows
    // everywhere else. Both are in the markup, so a mode switch waits on no
    // fetch and CSS alone picks one. `mode` pins one, for the editor's preview.
    const plateInset = mark.plate ? 8 : 0;
    const darkHeight = height - plateInset;
    const showLight = mode === "light" ? "block" : mode === "dark" ? "hidden" : "hidden [.light_&]:block";
    const showDark = mode === "dark" ? "inline-flex" : mode === "light" ? "hidden" : "inline-flex [.light_&]:hidden";
    const logoMax = maxWidth;
    // ⚠️ Compact lets the logo give way. The bar's brand zone is `w-64`, which
    // follows the member's density, and these sizes are px, which do not. At
    // compact density the zone is 224px, less than the 176px slot needs once
    // the fold column takes its share (measured 2026-10-09). Shrunk, the logo
    // keeps its shape (`object-contain`), so it only draws smaller.
    const fit = compact ? "min-w-0 shrink" : "shrink-0";
    const images = (
      <>
        {/* A `data:` URI from our own gateway, whose MIME type was derived from
            the file's magic bytes rather than from anything the uploader
            declared. `next/image` has nothing to optimise here and would only
            add a loader round-trip to bytes we already hold. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={mark.logo.dataUri}
          alt={mark.alt}
          style={{ height, width: logoBoxWidth(mark.logo, height, logoMax) }}
          className={`${showLight} ${fit} object-contain object-left`}
        />
        {/* The plate is white on purpose, in every theme: it is the brand's
            own backing, for a dark, colourful logo, not a surface of ours. */}
        <span
          className={`${showDark} ${fit} items-center ${mark.plate ? "rounded-md bg-white px-1.5" : ""}`}
          style={{ height }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={mark.logoDark.dataUri}
            alt={mark.alt}
            style={{
              height: darkHeight,
              width: logoBoxWidth(mark.logoDark, darkHeight, logoMax - (mark.plate ? 12 : 0)),
            }}
            className={compact ? "min-w-0 max-w-full object-contain object-left" : "object-contain object-left"}
          />
        </span>
      </>
    );
    return <span className="flex min-w-0 items-center">{images}</span>;
  }

  // ── No logo: our comet, and the organization's name (owner, 2026-10-11) ──
  //
  // The badge was a ⌘ glyph on a `bg-primary` tile, which is not our brand
  // and which an accent recoloured. It is the comet now, the same mark as the
  // bar's right end signs with (`MetoriteMark`). It keeps the tile's square box, so the
  // bar does not shift, and it has no fill: the comet carries its own colours.
  if (compact) {
    // D51: a logo-less organization still shows its OWN name, here on the
    // same line as our mark. With no organization yet, the product's name.
    return (
      <span className="flex min-w-0 items-center gap-2">
        <OurMark height={height} mode={mode} />
        <span className="min-w-0 truncate text-sm font-semibold tracking-tight text-foreground">
          {orgName.trim() || mark.title}
        </span>
      </span>
    );
  }

  return (
    <span className="flex min-w-0 items-center gap-2.5">
      <OurMark height={height} mode={mode} />
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold leading-tight tracking-tight text-sidebar-foreground">
          {mark.title}
        </span>
        <span className="block truncate text-[10px] leading-tight text-muted-foreground">
          {mark.caption}
        </span>
      </span>
    </span>
  );
}

/**
 * Our comet in the square box the no-logo lockup has always had. No fill and
 * no accent: the mark is the brand's, in every mode and under every accent.
 * Fence: `orgBrandLockup.mode.test.ts` "with no logo, the comet".
 */
function OurMark({ height, mode }: { height: number; mode?: "light" | "dark" }) {
  return (
    <span data-our-mark="" className="flex shrink-0 items-center justify-center" style={{ height, width: height }}>
      <MetoriteMark size={Math.round(height * 0.86)} mode={mode} />
    </span>
  );
}
