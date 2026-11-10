"use client";

/**
 * The byline of a page (WS-61 KB-2d, `knowledge_base.md` §21.7).
 *
 * "Edited by Meera, 3m ago", and a button with the count of the people who
 * wrote the page. The button opens the list of them, each with the count of
 * their versions and the time of the last one. Both read `versions`, which
 * the gateway bounds at the history floor (`lib/byline.ts`).
 */

import { useId, useState } from "react";

import { PersonName } from "@/components/PersonName";
import Button from "@/components/ui/Button";
import { relativeTime } from "@/lib/taskCard";

import { bylineOf, contributorsOf, versionCount } from "../lib/byline";
import type { PageVersion } from "../lib/types";
import ChromePopover from "./ChromePopover";

export default function PageByline({ versions }: { versions: readonly PageVersion[] }) {
  const byline = bylineOf(versions);
  const people = contributorsOf(versions);
  const [open, setOpen] = useState(false);
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const panelId = useId();
  if (!byline) return null;

  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-1 gap-y-0.5" data-page-byline="">
      <span className="flex min-w-0 items-center">
        <span className="mr-1 shrink-0">{byline.verb} by</span>
        <PersonName email={byline.email} className="min-w-0 max-w-40 text-foreground" />
        {byline.at ? <span className="shrink-0">, {relativeTime(byline.at) || "now"}</span> : null}
      </span>
      <span aria-hidden className="px-0.5">
        ·
      </span>
      <Button
        variant="ghost"
        size="sm"
        icon="Users"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={(e) => {
          setTrigger(e.currentTarget);
          setOpen((v) => !v);
        }}
      >
        {people.length === 1 ? "1 contributor" : `${people.length} contributors`}
      </Button>
      <ChromePopover
        trigger={trigger}
        open={open}
        onClose={() => setOpen(false)}
        label="Contributors to this page"
        id={panelId}
      >
        <ul className="flex flex-col" aria-label="Contributors">
          {people.map((p) => (
            <li key={p.email} className="flex items-center gap-2 rounded-md px-2 py-1.5">
              <span className="flex min-w-0 flex-1 flex-col">
                <PersonName email={p.email} className="text-sm text-foreground" />
                <span className="text-[11px] text-muted-foreground">
                  {versionCount(p.versions)}
                  {p.lastAt ? `, last ${relativeTime(p.lastAt) || "now"}` : ""}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </ChromePopover>
    </span>
  );
}
