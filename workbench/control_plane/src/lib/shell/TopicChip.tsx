"use client";

/**
 * The topic chip of a chat (WS-CO CO-2b, `chat_organization.md` §6, D163).
 *
 * The member presses the chip in the chat header. A menu opens with "New
 * topic…" and "No topic" first, at a fixed place, then the company topics
 * and the member's own topics. A pick moves the chat at once, and the chip
 * and the chat list show the new topic. The move is the member's own, and
 * the automatic pass never moves it back.
 *
 * It is `AnchoredPanel` from `src/components/ui/`, with `Button` rows and
 * `Input`, as `ChatMoreMenu` is. A press outside and Escape close it
 * (`lib/outsideClick.ts`). The list of topics comes through the one read
 * cache (`useCachedResource`), only while the menu is open.
 *
 * Three triggers:
 *
 * - `chip`: the topic name with the tag icon, in the header on a desktop.
 * - `icon`: the tag icon alone, in the narrow header of a phone.
 * - `none`: no trigger. The phone with the chat-first flag opens the menu
 *   from its "…" control (CF-8). `anchor` is that control: the menu hangs
 *   from it, a press on it counts as inside, and Escape gives it the focus.
 *
 * `chatTopics.ts` holds the rules and the requests. Fence (R7):
 * `chatTopics.test.ts`.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { invalidate } from "@/lib/dataCache";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import type { ChatSession, SessionTopic } from "@/lib/sessions";
import { useCachedResource } from "@/lib/useCachedResource";

import { shownTopic } from "./chatHistory";
import {
  FIXED_ROWS,
  NEW_TOPIC,
  NO_TOPIC,
  NO_TOPICS_YET,
  TOPICS_DID_NOT_LOAD,
  TOPICS_PATH,
  anchorPressCloses,
  chipLabel,
  createChatTopic,
  fetchChatTopics,
  focusAfterLoad,
  menuFocusIndex,
  topicMenuRows,
  type TopicMenuRow,
} from "./chatTopics";

export interface TopicChipProps {
  /** The chat on screen. */
  session: ChatSession | null;
  /** Move the chat into a topic, or to "No topic" with null. */
  onMove: (topic: SessionTopic | null) => void;
  trigger: "chip" | "icon" | "none";
  /** A phone: each row takes the tap size, 44 px. */
  phone?: boolean;
  /** Controlled open, for the `none` trigger. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** What the menu hangs from, for the `none` trigger. */
  anchor?: HTMLElement | null;
  /** Layout classes of the trigger. */
  className?: string;
}

/** The row that takes the focus (`menuFocusIndex`). */
const FOCUS_ROW = "[data-chat-topic-menu] [data-chat-topic-focus]";

/**
 * A group heading of the menu. It has the type of the group headings of the
 * chat list (`SessionPicker.tsx`, visual review P3), so a topic name reads
 * the same in the menu and in the list. No shared token holds those classes,
 * so the list's class string is used as it is.
 */
function Heading({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[11px] font-medium text-muted-foreground glass:text-caption">
      {children}
    </div>
  );
}

export default function TopicChip({
  session,
  onMove,
  trigger,
  phone = false,
  open: openProp,
  onOpenChange,
  anchor: anchorProp,
  className = "",
}: TopicChipProps) {
  const [selfOpen, setSelfOpen] = useState(false);
  const open = openProp ?? selfOpen;
  const setOpen = onOpenChange ?? setSelfOpen;
  // The span around the trigger is the anchor: `Button` takes no ref.
  const [own, setOwn] = useState<HTMLSpanElement | null>(null);
  const anchor = trigger === "none" ? (anchorProp ?? null) : own;
  const [naming, setNaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const topics = useCachedResource(open ? TOPICS_PATH : null, fetchChatTopics);
  // The row that the first focus of this open went to, and whether the
  // focus already moved once after the list landed.
  const firstFocus = useRef<{ el: HTMLElement | null; reran: boolean }>({ el: null, reran: false });
  const current = session ? (shownTopic(session)?.id ?? null) : null;
  const rows = useMemo(() => topicMenuRows(topics.data, current), [topics.data, current]);
  const label = chipLabel(session);

  // Each open starts on the list, with no name typed.
  useEffect(() => {
    if (open) return;
    setNaming(false);
    setDraft("");
    setProblem(null);
  }, [open]);

  // A press outside closes it. The trigger counts as inside, so the press
  // that toggles it is not undone by the same press. With the `none`
  // trigger, the trigger is the "…" control that `anchor` names, never a
  // whole header (review P2-1). Escape closes it from wherever focus is, and
  // gives the focus back to that trigger (`ChatMoreMenu` does the same).
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(anchor))) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      event.preventDefault();
      setOpen(false);
      const back = anchor?.tagName === "BUTTON" ? anchor : anchor?.querySelector<HTMLElement>("button");
      back?.focus();
    };
    // A press on the "…" control of the chat-first phone closes this menu
    // and goes no further, so the More menu does not open under it
    // (`anchorPressCloses`). The capture phase on `document` runs before the
    // handlers of React, which listen on the root.
    const onAnchorClick = (event: MouseEvent) => {
      if (!anchorPressCloses(trigger, open) || !anchor) return;
      if (!(event.target instanceof Node) || !anchor.contains(event.target)) return;
      event.stopPropagation();
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("click", onAnchorClick, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("click", onAnchorClick, true);
    };
  }, [open, anchor, setOpen, trigger]);

  // Focus goes to the row of `menuFocusIndex` once the panel has drawn: the
  // current topic, else "New topic…", and never "No topic" (review P2-3).
  // Only while the menu is open: a menu that closes because the chat changed
  // takes no focus, so the composer keeps it.
  useEffect(() => {
    if (!open || naming) return;
    firstFocus.current = { el: null, reran: false };
    const id = requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(FOCUS_ROW);
      el?.focus();
      firstFocus.current.el = el;
    });
    return () => cancelAnimationFrame(id);
  }, [open, naming]);

  // A cold open (visual review P2): the list lands after the first focus, so
  // that focus sat on "New topic…". When the list lands, the focus moves to
  // the current topic ONCE, and only if it still sits on the first row
  // (`focusAfterLoad`). A member who moved the focus keeps it, and the name
  // form (`naming`) stops it.
  const loaded = topics.data !== undefined;
  useEffect(() => {
    if (!open || naming || !loaded) return;
    const first = firstFocus.current;
    const id = requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(FOCUS_ROW);
      const onFirstRow = first.el !== null && document.activeElement === first.el;
      if (focusAfterLoad({ reran: first.reran, onFirstRow, moves: target !== null && target !== first.el })) {
        target?.focus();
      }
      first.reran = true;
    });
    return () => cancelAnimationFrame(id);
  }, [open, naming, loaded]);

  const move = (topic: SessionTopic | null) => {
    setOpen(false);
    onMove(topic);
  };

  const create = async () => {
    const name = draft.trim();
    if (!name || busy) return;
    setBusy(true);
    setProblem(null);
    const made = await createChatTopic(name);
    setBusy(false);
    if ("problem" in made) {
      setProblem(made.problem);
      return;
    }
    invalidate(TOPICS_PATH);
    move(made.topic);
  };

  const row = phone ? "min-h-11" : "min-h-8";
  const rowClass = `${row} w-full gap-2 px-3 text-left text-sm`;
  const focusAt = menuFocusIndex(rows);

  const renderRow = (r: TopicMenuRow, i: number) => {
    if (r.kind === "heading") return <Heading key={`h:${r.label}`}>{r.label}</Heading>;
    if (r.kind === "loading" || r.kind === "empty") {
      // One line that says what the list holds, in a status region, so a
      // screen reader hears the answer when it lands (M9). It keeps the
      // height of a row, so the panel does not jump (M1).
      const words = r.kind === "empty" ? NO_TOPICS_YET : topics.error ? TOPICS_DID_NOT_LOAD : "Loading the topics…";
      return (
        <p key={r.kind} role="status" className={`${row} flex items-center px-3 text-xs text-muted-foreground`}>
          {words}
        </p>
      );
    }
    const selected = r.kind === "topic" || r.kind === "none" ? r.current : false;
    const text = r.kind === "topic" ? r.topic.name : r.kind === "none" ? NO_TOPIC : NEW_TOPIC;
    const icon = r.kind === "topic" ? "Tag" : r.kind === "none" ? "CircleSlash" : "Plus";
    return (
      <Button
        key={r.kind === "topic" ? `t:${r.topic.id}` : r.kind}
        type="button"
        role="menuitem"
        variant="ghost"
        size="none"
        layout="flex items-center"
        selected={selected}
        data-chat-topic-row={r.kind}
        data-chat-topic-focus={i === focusAt ? "" : undefined}
        onClick={() => {
          if (r.kind === "topic") move(r.topic);
          else if (r.kind === "none") move(null);
          else setNaming(true);
        }}
        className={rowClass}
      >
        <Icon name={icon} size={14} className="shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-foreground">{text}</span>
        {selected ? <Icon name="Check" size={14} className="shrink-0 text-primary" /> : null}
      </Button>
    );
  };

  return (
    <>
      {trigger === "none" ? null : (
        <span ref={setOwn} className={`inline-flex min-w-0 shrink ${className}`}>
          <Button
            type="button"
            variant="ghost"
            size={trigger === "icon" ? "icon-sm" : "sm"}
            icon="Tag"
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label={`Topic: ${label}. Move this chat`}
            title="Move this chat to a topic"
            data-chat-topic-chip={current ? "set" : "none"}
            onClick={() => setOpen(!open)}
            className="min-w-0"
          >
            {trigger === "chip" ? <span className="min-w-0 max-w-32 truncate">{label}</span> : null}
          </Button>
        </span>
      )}
      <AnchoredPanel
        anchor={anchor}
        open={open}
        align="end"
        maxHeight={400}
        className="w-[min(18rem,calc(100vw-2rem))] p-1"
        panelProps={{ role: "menu", "aria-label": "Move this chat to a topic", "data-chat-topic-menu": "" }}
      >
        {naming ? (
          <form
            className="flex flex-col gap-2 p-2"
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <Input
              autoFocus
              inputSize={phone ? "lg" : "md"}
              value={draft}
              maxLength={40}
              onChange={(e) => {
                setDraft(e.target.value);
                setProblem(null);
              }}
              placeholder="Name of the topic"
              aria-label="Name of the new topic"
            />
            {problem ? (
              <p role="alert" className="text-xs text-destructive">
                {problem}
              </p>
            ) : (
              <p className="text-[11px] text-muted-foreground">Only you see this topic.</p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setNaming(false)}>
                Back
              </Button>
              <Button type="submit" variant="primary" size="sm" loading={busy} disabled={!draft.trim()}>
                Add and move
              </Button>
            </div>
          </form>
        ) : (
          <>
            {/* "New topic…" and "No topic" first, at a fixed place, so no
                row moves while the list loads (review P2-3). */}
            {rows.slice(0, FIXED_ROWS).map((r, i) => renderRow(r, i))}
            <div className="mt-1 border-t border-border pt-1" data-chat-topic-list="">
              {rows.slice(FIXED_ROWS).map((r, i) => renderRow(r, i + FIXED_ROWS))}
            </div>
          </>
        )}
      </AnchoredPanel>
    </>
  );
}
