"use client";

/**
 * People Center · one person, at their own address (H-145).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.2 — which has called
 * this "the person page" since 2026-08-06, while it was built as a side
 * panel keyed by React state.
 *
 * **What having no URL cost.** You could not send a colleague a link to a
 * colleague. The org chart could not open anybody — its nodes only expanded
 * and collapsed. `rg "/people/"` over the whole Projects app returned zero
 * hits, so an assignee had nowhere to point. Back and refresh both lost
 * whoever was open.
 *
 * **It renders `PersonPanel`, not a copy of it.** The panel already fetches
 * the record and the open work and lays out all six sections; it gained a
 * `page` variant in the same change, which swaps the side-panel frame for a
 * document one and drops the close control. A second implementation would be
 * two places for a field to go missing from — the argument that file already
 * makes about `ProfilePanels`, applied one level up.
 *
 * 📌 **No editor here.** Editing needs the directory (for the manager
 * picker) and the status vocabulary, both of which belong to the directory
 * page. The Edit control is therefore absent rather than broken, and an
 * administrator edits from the directory as before. Giving this page its own
 * editor means fetching both lists to serve a button, which is a bigger
 * change than a link deserved.
 */

import Link from "next/link";
import { use } from "react";

import Icon from "@/components/Icon";

import { PersonPanel } from "../components/PersonPanel";
import { PAGE_FRAME } from "../lib/frame";

export default function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  return (
    <main className={PAGE_FRAME}>
      {/* The way back is stated, not assumed. The tab bar above marks
          "Directory" as current for every `/people/…` route that has no tab
          of its own, so a person needs to say where they came from. */}
      <Link
        href="/people"
        className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <Icon name="ChevronLeft" size={14} />
        Directory
      </Link>
      <PersonPanel personId={id} variant="page" />
    </main>
  );
}
