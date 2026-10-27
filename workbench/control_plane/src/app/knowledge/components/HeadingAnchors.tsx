"use client";

/**
 * A copy-link anchor beside each heading of the body (WS-61 KB-2d, §21.7).
 *
 * ⚠️ The anchors draw in a layer OVER the editor, never inside it.
 * ProseMirror owns the DOM of the body, and it puts back a node that a
 * script adds. So this layer measures where each heading is, and draws a
 * `#` button in the gutter at that height. The pointer over a heading shows
 * its anchor, and Tab reaches each one. A click copies the address of the
 * heading, `…/pages/<id>#<anchor>`, and that address scrolls to the heading
 * when it opens (`usePageHeadings`).
 *
 * The gutter is narrow on a phone, so the layer draws from 640 px up.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";

import { headingHref } from "../lib/links";
import type { OutlineHeading } from "../lib/outline";

interface Spot {
  top: number;
  height: number;
}

export default function HeadingAnchors({
  pageId,
  headings,
  elements,
  children,
}: {
  pageId: string;
  headings: readonly OutlineHeading[];
  elements: readonly HTMLElement[];
  /** The body. The layer draws over it. */
  children: ReactNode;
}) {
  const toast = useToast();
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [spots, setSpots] = useState<Spot[]>([]);
  const [hover, setHover] = useState<number | null>(null);

  const measure = useCallback(() => {
    if (!box) return;
    const top = box.getBoundingClientRect().top;
    setSpots(
      elements.map((el) => {
        const r = el.getBoundingClientRect();
        return { top: r.top - top, height: Math.min(r.height, 32) };
      }),
    );
  }, [box, elements]);

  useEffect(() => {
    if (!box) return;
    // A new observer reports the size once at the start, so this measures
    // now, and again each time the body changes size (an image loads).
    const sizes = new ResizeObserver(() => measure());
    sizes.observe(box);
    return () => sizes.disconnect();
  }, [box, measure]);

  async function copy(index: number) {
    const h = headings[index];
    if (!h) return;
    try {
      await navigator.clipboard.writeText(new URL(headingHref(pageId, h.slug), window.location.origin).href);
      toast.show({ key: `kb-anchor:${h.slug}`, title: "Link to the heading copied" });
    } catch {
      toast.show({
        key: `kb-anchor:${h.slug}`,
        variant: "error",
        title: "Could not copy the link",
        description: "The browser refused the clipboard. Copy the address from the page instead.",
      });
    }
  }

  return (
    <div
      ref={setBox}
      className="relative"
      onMouseMove={(e) => {
        const target = e.target as HTMLElement;
        const anchor = target.closest<HTMLElement>("[data-heading-anchor]");
        if (anchor) return;
        const heading = target.closest<HTMLElement>("[data-md-level]");
        const at = heading ? elements.indexOf(heading) : -1;
        setHover(at >= 0 ? at : null);
      }}
      onMouseLeave={() => setHover(null)}
    >
      {children}
      {spots.map((spot, i) =>
        headings[i] ? (
          <div
            key={headings[i].slug}
            data-heading-anchor=""
            className="absolute -left-6 hidden w-6 items-center justify-center sm:flex"
            style={{ top: spot.top, height: spot.height }}
          >
            <Button
              variant="ghost"
              size="icon-xs"
              icon="Link"
              aria-label={`Copy the link to the heading ${headings[i].text}`}
              title="Copy the link to this heading"
              className={`tech-transition focus-visible:opacity-100 ${hover === i ? "opacity-100" : "opacity-0"}`}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              onClick={() => void copy(i)}
            />
          </div>
        ) : null,
      )}
    </div>
  );
}
