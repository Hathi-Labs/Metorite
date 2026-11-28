"use client";

/**
 * Projects · the left half of the page header (HDR-1, `one_projects_app.md`
 * §9e.3).
 *
 * Two lines. The first holds the place, the title and nothing else, so a long
 * description never takes its room. The second is one plain line made from the
 * description by `projectSummary`, then "More", then the facts ("3
 * sub-projects"). "More" opens "About this project", which draws the whole
 * description. Below 640 px the second line hides, and a tap on the title opens
 * About, as a sheet.
 *
 * About is a popover on a desktop and a sheet on a phone. Both are the house
 * primitives (`AnchoredPanel` and `Modal placement="sheet"`), as in
 * `SheetButton`. The colours and sizes come from the tokens.
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { MarkdownBody } from "@/components/MarkdownMessage";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { useDismiss } from "@/hooks/useDismiss";

/** The phone rule of HDR-1: below Tailwind's `sm`. */
const NARROW_QUERY = "(max-width: 639px)";

export const ABOUT_TITLE = "About this project";

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(NARROW_QUERY);
    const sync = () => setNarrow(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  return narrow;
}

export interface ProjectHeaderTitleProps {
  title: string;
  /** The muted name before the title: the space of a project, the project of a sub-project. */
  parent?: { name: string; onOpen: () => void } | null;
  /** The lock beside the title of the private space. */
  lockHint?: string | null;
  /** The plain line under the title. Empty or absent: no line. */
  line?: string | null;
  /** The facts after the line, such as "3 sub-projects". */
  facts?: string | null;
  /** The whole stored description. Empty or absent: no About. */
  description?: string | null;
  /** True when the description holds more than the line, so "More" shows. */
  more?: boolean;
}

export function ProjectHeaderTitle({
  title,
  parent = null,
  lockHint = null,
  line = null,
  facts = null,
  description = null,
  more = false,
}: ProjectHeaderTitleProps) {
  const narrow = useNarrow();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const about = Boolean(description && description.trim());
  const close = () => setOpen(false);
  useDismiss(open && !narrow, close, root);

  const hasSecondLine = Boolean(line) || more || Boolean(facts);

  return (
    <div ref={root} className="@container flex min-w-0 flex-1 flex-col" data-project-header="">
      <div className="flex min-w-0 items-baseline gap-1.5">
        {parent ? (
          // In a narrow pane the place folds away, so the title keeps its room.
          <span className="hidden min-w-0 max-w-[40%] shrink-[4] items-baseline gap-1.5 @[24rem]:flex">
            <Button
              variant="text"
              size="none"
              className="min-w-0 truncate text-sm"
              title={`Open ${parent.name}`}
              data-header-parent=""
              onClick={parent.onOpen}
            >
              <span className="truncate">{parent.name}</span>
            </Button>
            <span className="shrink-0 text-sm text-muted-foreground" aria-hidden="true">
              ›
            </span>
          </span>
        ) : null}
        <h2
          className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-foreground glass:text-subhead glass:font-semibold"
          data-header-title=""
        >
          {narrow && about ? (
            <Button
              variant="text"
              size="none"
              className="min-w-0 truncate text-left text-foreground!"
              title={title}
              aria-haspopup="dialog"
              aria-expanded={open}
              data-header-title-open=""
              onClick={(event) => {
                setAnchor(event.currentTarget);
                setOpen((now) => !now);
              }}
            >
              <span className="truncate">{title}</span>
            </Button>
          ) : (
            <span className="min-w-0 truncate" title={title}>
              {title}
            </span>
          )}
          {lockHint ? (
            <Icon name="Lock" className="h-3 w-3 shrink-0 text-muted-foreground" role="img" aria-label={lockHint} />
          ) : null}
        </h2>
      </div>
      {hasSecondLine ? (
        <p
          className="mt-0.5 hidden min-w-0 items-baseline gap-1 text-xs text-muted-foreground sm:flex"
          data-header-line=""
        >
          {line ? (
            <span className="min-w-0 truncate" title={line}>
              {line}
            </span>
          ) : null}
          {more && about ? (
            <Button
              variant="text"
              size="none"
              className="shrink-0 text-xs text-primary"
              aria-haspopup="dialog"
              aria-expanded={open}
              data-header-more=""
              onClick={(event) => {
                setAnchor(event.currentTarget);
                setOpen((now) => !now);
              }}
            >
              More
            </Button>
          ) : null}
          {facts ? (
            <span className="shrink-0" data-header-facts="">
              {line || (more && about) ? "· " : ""}
              {facts}
            </span>
          ) : null}
        </p>
      ) : null}
      {about ? (
        narrow ? (
          <Modal open={open} onClose={close} title={ABOUT_TITLE} placement="sheet" size="md" className="max-h-[85vh]">
            <div className="min-h-0 min-w-0 max-w-full overflow-x-hidden overflow-y-auto p-3 text-sm [&_table]:w-max [&_table]:min-w-full" data-header-about="">
              <MarkdownBody content={description as string} />
            </div>
          </Modal>
        ) : (
          <AnchoredPanel
            anchor={anchor}
            open={open}
            align="start"
            maxHeight={560}
            className="w-[34rem] max-w-[90vw] max-h-[560px]"
            panelProps={{ role: "dialog", "aria-label": ABOUT_TITLE }}
          >
            <div className="min-w-0 max-w-full p-3 text-sm [&_table]:w-max [&_table]:min-w-full" data-header-about="">
              <p className="mb-2 text-xs font-medium text-muted-foreground">{ABOUT_TITLE}</p>
              <MarkdownBody content={description as string} />
            </div>
          </AnchoredPanel>
        )
      ) : null}
    </div>
  );
}

/**
 * The title of the phone bar (`AppTopBar compact`). A phone has no header
 * under the bar, so a tap on the title opens About as a sheet. A project with
 * no description draws the plain title.
 */
export function PhoneProjectTitle({ title, description = null }: { title: string; description?: string | null }) {
  const [open, setOpen] = useState(false);
  if (!description || !description.trim()) return <>{title}</>;
  return (
    <>
      <Button
        variant="text"
        size="none"
        className="max-w-full truncate text-left text-sm font-medium text-foreground!"
        title={title}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-header-title-open=""
        onClick={() => setOpen(true)}
      >
        <span className="truncate">{title}</span>
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title={ABOUT_TITLE} placement="sheet" size="md" className="max-h-[85vh]">
        <div className="min-h-0 min-w-0 max-w-full overflow-x-hidden overflow-y-auto p-3 text-sm [&_table]:w-max [&_table]:min-w-full" data-header-about="">
          <MarkdownBody content={description} />
        </div>
      </Modal>
    </>
  );
}

export default ProjectHeaderTitle;
