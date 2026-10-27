"use client";

/**
 * The chat's one "…" control on a phone (WS-62 CF-8, `chat_first_surface.md`
 * §4.8, flag `NEXT_PUBLIC_CHAT_FIRST`).
 *
 * A phone sheet shows one header row, one field and one Send. Everything
 * the composer and the two rows above the thread used to show sits here,
 * reachable in one tap and never in view: the assistant's status, Share (a
 * room act), the room rail, the effort choice (D90 keeps it), the context
 * window, and the model picker where an agent still has one.
 *
 * It is `AnchoredPanel` from `src/components/ui/`, hung from the control at
 * the composer's end, with `Button` rows at 44 px (the phone's tap size).
 * The model list is a second page of the same panel, with a way back, so
 * a long list never pushes the other rows off the screen.
 *
 * It decides nothing: `AgentChat` owns every fact and every act, and this
 * file draws them. A desktop, and a phone with the flag off, never mount
 * it.
 *
 * Fences (R7): `src/lib/shell/assistant.test.ts` ("the chat's chrome on a
 * phone") and `e2e/assistant-motion.spec.ts`.
 */

import { useEffect, useState } from "react";

import ContextRing from "@/components/ContextRing";
import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import type { ThinkMode } from "@/lib/tierRouting";

export interface ChatMoreMenuProps {
  /** The assistant's name and whether a run goes on. */
  status: { label: string; active: boolean };
  /**
   * Acts the host lends to the menu, such as the dock's "This chat's files"
   * when the header row is too tight for it at 390 px.
   */
  acts?: readonly { icon: string; label: string; onClick: () => void }[];
  /** Share this conversation, or manage the room. Null while the room is unknown. */
  share: { shared: boolean; onOpen: () => void } | null;
  /** The room's own rail, only in a room. */
  roomRail: { open: boolean; onToggle: () => void } | null;
  /** The effort choice. Null for an agent the tier policy covers (D90). */
  effort: {
    current: ThinkMode;
    options: readonly { mode: ThinkMode; label: string; title: string }[];
    onPick: (mode: ThinkMode) => void;
  } | null;
  /** The context window. */
  context: {
    pct: number;
    usedTokens: number;
    totalTokens: number;
    compacting: boolean;
    onCompact?: () => void;
    modelId?: string;
  };
  /** The model picker, where an agent still has one. */
  model: {
    label: string;
    current: string;
    groups: readonly string[];
    models: readonly { id: string; label: string; group: string }[];
    onPick: (id: string) => void;
  } | null;
}

/** The phone's tap size, on every row and on the control itself. */
const ROW = "min-h-11 w-full gap-3 px-3 text-left text-sm";

function Row({
  icon,
  label,
  hint,
  selected,
  onClick,
  trailing,
}: {
  icon?: string;
  label: string;
  hint?: string;
  selected?: boolean;
  onClick: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      role="menuitem"
      variant="ghost"
      size="none"
      layout="flex items-center"
      selected={selected}
      onClick={onClick}
      className={ROW}
    >
      {icon ? <Icon name={icon} size={16} className="shrink-0" /> : null}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-foreground">{label}</span>
        {hint ? <span className="block truncate text-[11px] text-muted-foreground">{hint}</span> : null}
      </span>
      {trailing}
      {selected ? <Icon name="Check" size={14} className="shrink-0 text-primary" /> : null}
    </Button>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-0.5 pt-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

export default function ChatMoreMenu({ status, acts = [], share, roomRail, effort, context, model }: ChatMoreMenuProps) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<"root" | "model">("root");
  // The span around the control is the anchor: `Button` takes no ref, and
  // the span is the same box.
  const [anchor, setAnchor] = useState<HTMLSpanElement | null>(null);

  // Every open starts on the first page.
  useEffect(() => {
    if (!open) setPage("root");
  }, [open]);

  // A tap outside closes it. The control counts as inside, so the tap that
  // toggles it is not undone by the same tap (`StatusMenu` does the same).
  // Escape closes it too, from wherever focus is: the panel mounts a frame
  // after the tap, so focus is still on the control then. The listener is
  // in the capture phase, so the sheet's own Escape rule never sees it.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(anchor))) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      event.preventDefault();
      if (page === "model") {
        setPage("root");
        return;
      }
      setOpen(false);
      anchor?.querySelector<HTMLElement>("button")?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, page, anchor]);

  // Focus goes to the first row once the panel has drawn, so a keyboard
  // and a screen reader land in the menu.
  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      document.querySelector<HTMLElement>("[data-chat-more-menu] [role='menuitem']")?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [open, page]);

  const close = () => setOpen(false);
  const pick = (act: () => void) => () => {
    act();
    close();
  };

  return (
    <>
      <span ref={setAnchor} className="inline-flex shrink-0">
        <Button
          type="button"
          variant="ghost"
          size="none"
          icon="MoreHorizontal"
          aria-label="More"
          aria-haspopup="menu"
          aria-expanded={open}
          title="More"
          data-chat-more=""
          onClick={() => setOpen((v) => !v)}
          className="h-11 w-11"
        />
      </span>
      <AnchoredPanel
        anchor={anchor}
        open={open}
        align="end"
        maxHeight={440}
        className="w-[min(20rem,calc(100vw-2rem))] p-1"
        panelProps={{
          role: "menu",
          "aria-label": "More",
          "data-chat-more-menu": page,
        }}
      >
        {page === "model" && model ? (
          <>
            <Row icon="ChevronLeft" label="Back" onClick={() => setPage("root")} />
            {model.groups.map((group) => (
              <div key={group}>
                <Heading>{group}</Heading>
                {model.models
                  .filter((m) => m.group === group)
                  .map((m) => (
                    <Row
                      key={m.id}
                      label={m.label}
                      selected={m.id === model.current}
                      onClick={pick(() => model.onPick(m.id))}
                    />
                  ))}
              </div>
            ))}
          </>
        ) : (
          <>
            {/* The assistant's status: the one line the agent row used to show. */}
            <div
              role="status"
              data-chat-more-status=""
              className="flex min-h-11 items-center gap-3 px-3 text-sm"
            >
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${status.active ? "bg-warning motion-safe:animate-pulse" : "bg-success"}`}
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1 truncate text-foreground">{status.label}</span>
              <span className="shrink-0 text-[11px] text-muted-foreground">{status.active ? "working" : "ready"}</span>
            </div>
            {acts.map((a) => (
              <Row key={a.label} icon={a.icon} label={a.label} onClick={pick(a.onClick)} />
            ))}
            {share ? (
              <Row
                icon={share.shared ? "Users" : "Share2"}
                label={share.shared ? "Manage the room" : "Share this conversation"}
                onClick={pick(share.onOpen)}
              />
            ) : null}
            {roomRail ? (
              <Row
                icon="PanelRight"
                label={roomRail.open ? "Hide the room" : "Show the room"}
                onClick={pick(roomRail.onToggle)}
              />
            ) : null}
            {effort ? (
              <>
                <Heading>Effort</Heading>
                {effort.options.map((o) => (
                  <Row
                    key={o.mode}
                    label={o.label}
                    hint={o.title}
                    selected={o.mode === effort.current}
                    onClick={pick(() => effort.onPick(o.mode))}
                  />
                ))}
              </>
            ) : null}
            {model ? (
              <>
                <Heading>Model</Heading>
                <Row
                  icon="Cpu"
                  label={model.label}
                  hint="Change the model"
                  onClick={() => setPage("model")}
                  trailing={<Icon name="ChevronRight" size={14} className="shrink-0 text-muted-foreground" />}
                />
              </>
            ) : null}
            <Heading>Context</Heading>
            <div className="flex min-h-11 items-center gap-3 px-3 text-sm" data-chat-more-context="">
              <ContextRing
                pct={context.pct}
                usedTokens={context.usedTokens}
                totalTokens={context.totalTokens}
                compacting={context.compacting}
                onCompact={context.onCompact}
                modelId={context.modelId}
              />
              <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                {context.usedTokens.toLocaleString()} of {context.totalTokens.toLocaleString()} tokens
              </span>
            </div>
          </>
        )}
      </AnchoredPanel>
    </>
  );
}
