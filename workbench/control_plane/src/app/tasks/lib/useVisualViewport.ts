import { useEffect, useState } from "react";

import { phoneBarTop } from "@/components/ui/AnchoredPanel";

/**
 * MOB-9 (task 2002): the phone's bottom bar paints above the overlay, so the
 * height stops at the bar's top edge. With the keyboard up, the visual
 * viewport ends above the bar and nothing is taken off.
 */
export function heightAboveBar(vv: { height: number; offsetTop: number }, barTop: number | null): number {
  if (barTop === null) return vv.height;
  return Math.max(0, vv.height - Math.max(0, vv.offsetTop + vv.height - barTop));
}

/**
 * Track the mobile **visual viewport** so bottom-sheets can stay above the
 * on-screen keyboard. When the keyboard opens it shrinks the visual viewport
 * (not the layout viewport), so a `fixed inset-0` overlay would otherwise be
 * partly hidden behind it. Size the overlay to the returned height/top and its
 * `items-end` content sits just above the keyboard.
 *
 * Returns `null` until measured (and on browsers without `visualViewport`), so
 * callers fall back to full-screen. Reads happen in an async frame to avoid a
 * synchronous set-state during the effect.
 */
export function useVisualViewport(): { height: number; top: number } | null {
  const [vp, setVp] = useState<{ height: number; top: number } | null>(null);

  useEffect(() => {
    const vv = typeof window !== "undefined" ? window.visualViewport : null;
    if (!vv) return;
    const update = () => setVp({ height: heightAboveBar(vv, phoneBarTop()), top: vv.offsetTop });
    const raf = requestAnimationFrame(update); // initial read, off the effect tick
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(raf);
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);

  return vp;
}
