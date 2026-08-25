"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useEffect, useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import {
  originEmailHref,
  DISPOSITION_LABEL,
  durationLabel,
  formatStatus,
  initials,
  isOverdue,
  relativeTime,
} from "../lib/utils";
import { Disposition, Energy, GtdItem, Person } from "../lib/types";
import { syncBadge } from "../lib/syncState";
import { TaskMeta } from "@/components/TaskMeta";
import {
  apiItemDetail,
  type ProviderTaskDetail,
  type TaskComment,
  type TaskSubtask,
} from "../lib/api";
import { SourceBadge } from "./SourceBadge";
import { AttachmentChips } from "./AttachmentComposer";
import { ClarifyPanel } from "./ClarifyPanel";
import { AiTaskActions } from "./AiTaskActions";
import { DelegateDialog } from "./DelegateDialog";
import { WeightToggles, PriorityBadge, SuggestionBadge } from "./PriorityControls";
import { isUntagged } from "../lib/priority";
import { isWaitingOverdue } from "../lib/waiting";
import { useCardActions } from "../lib/useCardActions";

// Ages/overdue on this panel are read against the REAL clock — `isOverdue` and
// `relativeTime` both default their `nowMs` to Date.now(). They used to be
// passed a frozen `MOCK_NOW = Date.UTC(2026, 5, 30, …)` demo constant, so every
// label here — including a delegated item's "since" — drifted one more day off
// the truth every day.

const ENERGY_DOT: Record<Energy, string> = {
  low: "bg-success",
  medium: "bg-warning",
  high: "bg-destructive",
};

// Disposition → accent, so a processed task reads like a real status chip.
const DISP_TONE: Record<Disposition, string> = {
  INBOX: "bg-secondary text-muted-foreground",
  NEXT: "bg-primary/15 text-primary",
  WAITING: "bg-warning/15 text-warning",
  SOMEDAY: "bg-secondary text-muted-foreground",
  PROJECT: "bg-primary/15 text-primary",
  REFERENCE: "bg-secondary text-muted-foreground",
  DONE: "bg-success/15 text-success",
  TRASH: "bg-destructive/15 text-destructive",
};

const ESTIMATES = [5, 15, 30, 60, 120, 240];

/**
 * The docked detail pane's entry point — it reads the store's `selectedItemId`
 * and picks the surface that item needs. Mounted by `tasks/page.tsx` as the
 * third column beside the item list (the house layout, DESIGN_SYSTEM §6), the
 * same composition `projects/page.tsx` uses for `TaskPanel`.
 *
 * `onMaximize` raises the same detail into `TaskFocusModal` for the reading
 * width the pane cannot give; `onClose` dismisses the pane. Both are optional —
 * without them the maximise button falls back to the store's `openFocus` and no
 * ✕ is drawn, so any other mount site keeps working.
 */
export function ItemDetail({
  onMaximize,
  onClose,
}: {
  onMaximize?: () => void;
  onClose?: () => void;
} = {}) {
  const items = useTaskStore((s) => s.items);
  const backend = useTaskStore((s) => s.backend);
  const selectedItemId = useTaskStore((s) => s.selectedItemId);

  const item = selectedItemId
    ? items.find((i) => i.id === selectedItemId)
    : undefined;

  // Inbox items get the Clarify decision tree (F2). A clarified task gets the
  // editable detail view below. Keyed by id so state resets per item.
  //
  // Reachable in the docked pane the ordinary way: select an inbox item, then
  // switch to a task view — `selectedItemId` survives the view change. The
  // clarify tree carries no header of its own, so the pane's ✕ has to be added
  // here or that selection would be undismissable.
  if (item && item.disposition === "INBOX") {
    if (!onClose) return <ClarifyPanel key={item.id} item={item} />;
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-end border-b border-border bg-card px-2 py-1">
          <Button
            variant="ghost"
            size="icon-xs"
            icon="X"
            title="Close detail"
            aria-label="Close detail"
            onClick={onClose}
          />
        </div>
        <div className="min-h-0 flex-1">
          <ClarifyPanel key={item.id} item={item} />
        </div>
      </div>
    );
  }

  if (item) {
    return (
      <TaskDetail
        key={item.id}
        item={item}
        backend={backend}
        onMaximize={onMaximize}
        onClose={onClose}
      />
    );
  }

  // Reachable with a selection that no longer resolves — a hydrate/workspace
  // switch replaces `items` while `selectedItemId` survives (taskStore keeps it
  // across a reload; only the delete paths clear it).
  return <ItemDetailEmpty />;
}

function ItemDetailEmpty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <AppIcon name="FolderKanban" className="h-8 w-8 text-muted-foreground/40" />
      <p className="text-sm text-muted-foreground">
        Select an item to see its details.
      </p>
    </div>
  );
}

// ── The editable task view ──────────────────────────────────────────────────

export function TaskDetail({
  item,
  backend,
  focused,
  onMaximize,
  onClose,
}: {
  item: GtdItem;
  backend: string;
  /** true when rendered inside the full-page focus overlay (hides the
   *  expand button; wider content handled by the modal wrapper). */
  focused?: boolean;
  /** Docked pane only: raise this same detail into the focus overlay.
   *  Omitted → the button falls back to the store's `openFocus`. */
  onMaximize?: () => void;
  /** Docked pane only: dismiss the pane and give the width back to the list.
   *  Omitted → no ✕ is drawn (the overlay has its own). */
  onClose?: () => void;
}) {
  const projects = useTaskStore((s) => s.projects);
  const contexts = useTaskStore((s) => s.contexts);
  const people = useTaskStore((s) => s.people);
  const urgentWindowHours = useTaskStore((s) => s.settings.urgentWindowHours);
  const updateItem = useTaskStore((s) => s.updateItem);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const archiveItem = useTaskStore((s) => s.archiveItem);
  const openFocus = useTaskStore((s) => s.openFocus);
  const enterFocusSession = useTaskStore((s) => s.enterFocusSession);
  const isArchived = !!item.archivedAt;

  const [pushState, setPushState] = useState<"idle" | "busy" | string>("idle");
  // Delegating a LOCAL task opens the "create in ClickUp under a project" flow
  // (a teammate can't be assigned to a private local task). Holds the picked
  // person until the destination dialog resolves.
  const [delegateTo, setDelegateTo] = useState<Person | null>(null);
  // After reassigning/unassigning a task that's currently in MY Next Actions,
  // offer to drop it from my list (is_mine=false) — it stays on ClickUp. This
  // component is keyed by item.id at every call site, so it remounts per task
  // and the offer resets to false on its own (no reset effect needed).
  const [offerDropFromNext, setOfferDropFromNext] = useState(false);

  const project = item.projectId
    ? projects.find((p) => p.id === item.projectId)
    : undefined;
  const overdue = isOverdue(item);
  const isSynced = item.source === "SYNCED";
  // ⚠️ `account` is gone with the connectors (D52, WS-39 S3a-client slice 4),
  // and with it the two things it fed: the delegate picker's workspace-member
  // list, and a synced task's stage options. People now come from the
  // directory alone — which is the correct answer under one store, and worth
  // saying rather than leaving as an unexplained simplification.
  const memberPeople: Person[] = people;
  // The local Kanban stage (the workflow-stage axis of My Next Actions). Used to
  // show/change a LOCAL task's stage in the detail — synced tasks show their raw
  // ClickUp status instead (below). setStage handles done/reopen like the card.
  const stageActions = useCardActions(item);
  // The full owner set (falls back to the single assignee for older rows / mock).
  const assigneeList: Person[] =
    item.assignees ?? (item.assignee ? [item.assignee] : []);

  // The read-only half of the sync lifecycle. `canPush` is deliberately NOT
  // consulted any more: there is nothing to push to (D52), so the affordance it
  // gated was a button whose only possible outcome was a 400 — the same class
  // as S1's deleted Connect and Sync controls. The BADGE stays, because a row
  // imported before the retirement really is a frozen mirror and saying so is
  // information, not a control.
  const sync = syncBadge(item.syncState);

  const dueValue = item.dueAt ? item.dueAt.slice(0, 10) : "";
  // The promised-by date on the waiting-for record (null = no promise made).
  // `promiseLate` highlights only an EXPLICIT promise that has passed — when
  // there is none the Due field above already carries the overdue styling, so
  // the panel never says the same thing twice in two places.
  const promisedValue = item.expectedBy ? item.expectedBy.slice(0, 10) : "";
  const promiseLate = !!item.expectedBy && isWaitingOverdue(item);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background">
      {/* Header — status chip, source, deep link, editable title */}
      <div className="border-b border-border bg-card px-5 py-4">
        {/* focused → the modal's × occupies the top-right corner; keep the
            archive/delete actions clear of it */}
        <div className={`mb-2 flex flex-wrap items-center gap-2 ${focused ? "pr-9" : ""}`}>
          {/* Status is changed through the status chip (disposition) and the
              Stage card below — not a standalone Done button, which read as
              "this task is complete" even when it wasn't. */}
          <StatusPicker item={item} onPick={(d) => quickDispose(item.id, d)} />
          <SourceBadge source={item.source} provider={item.provider} />
          {isSynced && item.providerUrl && (
            <a
              href={item.providerUrl}
              target="_blank"
              rel="noreferrer"
              className="tech-transition inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
            >
              <AppIcon name="ExternalLink" className="h-3 w-3" />
              Open in {item.provider}
            </a>
          )}
          {/* Start (or restart) a Focus-Mode session on this task — the room +
              timer open globally and can be minimized to the dock. Actionable
              open tasks only. */}
          {(item.disposition === "NEXT" || item.disposition === "PROJECT") && (
            <button
              type="button"
              onClick={() => enterFocusSession(item.id)}
              title="Start Focus Mode — a full-screen timer for this task"
              className="tech-transition inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/10"
            >
              <AppIcon name="Timer" className="h-3 w-3" />
              Focus
            </button>
          )}
          {/* Maximise — the docked pane is 380px and this detail is dense
              (nine sections plus the provider ones), so the reading width the
              overlay gives is an affordance, not a leftover. Hidden inside the
              overlay itself, which is already the wide mode. */}
          {!focused && (
            <button
              type="button"
              title="Open full page"
              aria-label="Open full page"
              onClick={() => (onMaximize ? onMaximize() : openFocus(item.id))}
              className="tech-transition ml-auto rounded-md p-1 text-muted-foreground/70 hover:bg-secondary hover:text-foreground"
            >
              <AppIcon name="Maximize2" className="h-4 w-4" />
            </button>
          )}
          <button
            type="button"
            title={isArchived ? "Restore from archive" : "Archive task"}
            aria-label={isArchived ? "Restore from archive" : "Archive task"}
            onClick={() => archiveItem(item.id, !isArchived)}
            className={[
              "tech-transition rounded-md p-1 text-muted-foreground/70 hover:bg-secondary hover:text-foreground",
              focused ? "ml-auto" : "",
            ].join(" ")}
          >
            {isArchived ? (
              <AppIcon name="ArchiveRestore" className="h-4 w-4" />
            ) : (
              <AppIcon name="Archive" className="h-4 w-4" />
            )}
          </button>
          <button
            type="button"
            title="Delete task"
            aria-label="Delete task"
            onClick={() => requestDelete([item.id])}
            className="tech-transition rounded-md p-1 text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
          >
            <AppIcon name="Trash2" className="h-4 w-4" />
          </button>
          {/* Close the docked pane (Projects' TaskPanel closes from its own ✕
              too). The overlay draws its own ✕, so this stays off there. */}
          {!focused && onClose && (
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              title="Close detail"
              aria-label="Close detail"
              onClick={onClose}
            />
          )}
        </div>
        <EditableTitle
          value={item.title}
          onSave={(t) => updateItem(item.id, { title: t })}
        />
        {/* AI affordances — re-clarify (break down / refine) + fill missing
            details. Available for every task, local or ClickUp. Hidden for
            unprocessed inbox items (they clarify from the inbox flow). */}
        {item.disposition !== "INBOX" && (
          <div className="mt-2">
            <AiTaskActions item={item} />
          </div>
        )}
        {isSynced && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Edits sync back to {item.provider === "clickup" ? "ClickUp" : item.provider}.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-5 px-5 py-4">
        {/* Next action — the cardinal GTD field, prominent + editable */}
        <section>
          <SectionLabel icon={themedIcon("ArrowRight")}>Next action</SectionLabel>
          <EditableText
            value={item.nextAction ?? ""}
            placeholder="The next physical, visible step…"
            emptyHint="Add the next action"
            onSave={(v) => updateItem(item.id, { nextAction: v })}
          />
        </section>

        {/* Metadata grid — every cell is click-to-edit.
            ⚠️ Column count follows the SURFACE, not the viewport. This detail
            was built for the `max-w-3xl` modal, where two columns are
            comfortable; docking it into DESIGN_SYSTEM §6's 380px pane left each
            cell ~170px and its label and value collided (owner-reported after
            the S2 deploy, with a screenshot). A media query cannot fix it —
            the pane is 380px on a 4K monitor too — so the switch is `focused`,
            the prop that already distinguishes the two lives. */}
        <section>
          <SectionLabel icon={themedIcon("Tag")}>Details</SectionLabel>
          <div className={`grid gap-2 ${focused ? "grid-cols-2" : "grid-cols-1"}`}>
            {/* Context */}
            <MetaEdit label="Context" icon={themedIcon("Tag")}
              display={item.context
                ? <span className="font-mono text-primary/90">{item.context}</span>
                : null}
            >
              {(close) => (
                <ChipMenu
                  options={contexts.map((c) => c.name)}
                  active={item.context}
                  mono
                  allowClear
                  onPick={(v) => { updateItem(item.id, { context: v ?? "" }); close(); }}
                />
              )}
            </MetaEdit>

            {/* Energy */}
            <MetaEdit label="Energy" icon={themedIcon("Gauge")}
              display={item.energy
                ? <span className="inline-flex items-center gap-1.5 capitalize">
                    <span className={`h-2 w-2 rounded-full ${ENERGY_DOT[item.energy]}`} />
                    {item.energy}
                  </span>
                : null}
            >
              {(close) => (
                <ChipMenu
                  options={["low", "medium", "high"]}
                  active={item.energy}
                  capitalize
                  allowClear
                  onPick={(v) => { updateItem(item.id, { energy: (v as Energy) ?? undefined }); close(); }}
                />
              )}
            </MetaEdit>

            {/* Estimate */}
            <MetaEdit label="Estimate" icon={themedIcon("Zap")}
              display={item.timeEstimateMins
                ? <span>{durationLabel(item.timeEstimateMins)}</span>
                : null}
            >
              {(close) => (
                <ChipMenu
                  options={ESTIMATES.map((m) => durationLabel(m))}
                  active={item.timeEstimateMins ? durationLabel(item.timeEstimateMins) : undefined}
                  allowClear
                  onPick={(label) => {
                    const mins = label ? ESTIMATES[ESTIMATES.map((m) => durationLabel(m)).indexOf(label)] : 0;
                    updateItem(item.id, { timeEstimateMins: mins });
                    close();
                  }}
                />
              )}
            </MetaEdit>

            {/* Due */}
            <MetaEdit label="Due" icon={themedIcon("CalendarClock")}
              display={item.dueAt
                ? <span className={`inline-flex items-center gap-1 ${overdue ? "font-medium text-destructive" : ""}`}>
                    {overdue ? <AppIcon name="AlertTriangle" className="h-3 w-3" /> : <AppIcon name="Clock" className="h-3 w-3" />}
                    {relativeTime(item.dueAt)}
                  </span>
                : null}
            >
              {(close) => (
                <div className="flex items-center gap-1.5">
                  <input
                    type="date"
                    defaultValue={dueValue}
                    autoFocus
                    onChange={(e) => {
                      updateItem(item.id, {
                        dueAt: e.target.value ? new Date(e.target.value).toISOString() : "",
                      });
                      close();
                    }}
                    className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                  />
                  {item.dueAt && (
                    <button type="button" onClick={() => { updateItem(item.id, { dueAt: "" }); close(); }}
                      className="tech-transition rounded p-1 text-muted-foreground hover:text-destructive" title="Clear">
                      <AppIcon name="X" className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              )}
            </MetaEdit>

            {/* Stage — for a SYNCED task the raw ClickUp status (back-syncs on
                change); for a LOCAL task the workflow stage (the My Next Actions
                column). Local tasks always show one now, so their stage is
                visible/changeable here too, not just on the board. */}
            {isSynced ? (
              /* A frozen mirror keeps its last known status, READ-ONLY: the
                 picker's options came from the workspace and there is no
                 workspace (D52), so offering a change would write a value
                 nothing can honour. */
              item.providerStatus ? (
                <MetaEdit label="Stage" icon={themedIcon("CircleDot")}
                  display={<span>{formatStatus(item.providerStatus)}</span>}
                >
                  {() => null}
                </MetaEdit>
              ) : null
            ) : (
              stageActions.stages.length > 0 && (
                <MetaEdit label="Stage" icon={themedIcon("CircleDot")}
                  display={<span>{stageActions.currentStage}</span>}
                >
                  {(close) => (
                    <ChipMenu
                      options={stageActions.stages}
                      active={stageActions.currentStage}
                      onPick={(v) => { if (v) stageActions.setStage(v); close(); }}
                    />
                  )}
                </MetaEdit>
              )
            )}

            {/* Assignee(s). A SYNCED task supports MULTIPLE owners (ClickUp
                allows several) — a multi-select toggle list. A LOCAL task keeps
                the single-owner flow: assigning a teammate must first create a
                ClickUp task (they can't see a private local one), so it routes
                through the destination picker. */}
            {isSynced ? (
              <MetaEdit label="Assignees" icon={themedIcon("UserRound")}
                display={assigneeList.length
                  ? <AssigneeStack people={assigneeList} />
                  : null}
              >
                {() => (
                  <MultiPersonMenu
                    people={memberPeople}
                    active={assigneeList}
                    onToggle={(p) => {
                      const on = assigneeList.some((a) => samePerson(a, p));
                      const next = on
                        ? assigneeList.filter((a) => !samePerson(a, p))
                        : [...assigneeList, p];
                      updateItem(item.id, { assignees: next });
                    }}
                    onClear={() => updateItem(item.id, { assignees: [] })}
                  />
                )}
              </MetaEdit>
            ) : (
              <MetaEdit label="Assignee" icon={themedIcon("UserRound")}
                display={item.assignee
                  ? <span className="inline-flex items-center gap-1.5">
                      <Avatar name={item.assignee.name} />
                      {item.assignee.name}
                    </span>
                  : null}
              >
                {(close) => (
                  <PersonMenu
                    people={memberPeople}
                    active={item.assignee ?? null}
                    onPick={(p) => {
                      // A LOCAL task assigned to a teammate must become a ClickUp
                      // task (they can't see a private local one) → open the
                      // destination picker. Un-assigning (p=null) just patches.
                      if (p) {
                        setDelegateTo(p);
                      } else {
                        updateItem(item.id, { assignee: p });
                        if (item.disposition === "NEXT" && item.isMine) {
                          setOfferDropFromNext(true);
                        }
                      }
                      close();
                    }}
                  />
                )}
              </MetaEdit>
            )}

            {/* Project (read-only link for now — re-file happens via clarify) */}
            {project && (
              <div className="min-w-0 rounded-md border border-border bg-card px-3 py-2">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Project
                </div>
                <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-sm text-foreground">
                  <AppIcon name="FolderKanban" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                  <span className="truncate" title={project.outcome}>{project.outcome}</span>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Priority — the matrix inputs (Important/Leveraged manual, Urgent
            derived) + the computed cell. Not shown for unprocessed inbox items
            (they get prioritized in the clarify card). */}
        {item.disposition !== "INBOX" && (
          <section className="rounded-lg border border-border bg-card px-3 py-2.5">
            {/* Top-right of the sub-card: the priority cell pill and — right
                beside it — the competing action nudge (delegate / schedule /
                eliminate). Same suggestion the card carries, here in full: a
                suggestion, not a status; dismiss with its × ("keep mine"), and
                "Schedule?"/"Eliminate?" open their popups. */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Priority
              </span>
              <div className="flex flex-wrap items-center justify-end gap-1.5">
                <PriorityBadge
                  item={item}
                  urgentWindowHours={urgentWindowHours}
                />
                <SuggestionBadge
                  item={item}
                  urgentWindowHours={urgentWindowHours}
                />
              </div>
            </div>
            <div className="mt-2">
              <WeightToggles
                item={item}
                urgentWindowHours={urgentWindowHours}
                onChange={(w) => updateItem(item.id, w)}
              />
            </div>
            {isUntagged(item) && (
              <p className="mt-1.5 text-[11px] text-muted-foreground/70">
                Not yet judged — flag it important or leveraged, or leave it to
                default low priority.
              </p>
            )}
          </section>
        )}

        {/* Offer to drop a just-reassigned/unassigned task from My Next Actions.
            It stays on ClickUp — only the personal list changes. The regular
            delete/two-way sync is untouched. */}
        {offerDropFromNext && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5">
            <AppIcon name="AlertTriangle" className="h-4 w-4 shrink-0 text-warning" />
            <span className="min-w-0 flex-1 text-[12.5px] text-foreground">
              No longer your action? Remove it from My Next Actions — it stays on ClickUp.
            </span>
            <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => { updateItem(item.id, { isMine: false }); setOfferDropFromNext(false); }} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
              <AppIcon name="UserMinus" className="h-3.5 w-3.5" />
              Remove from My Next Actions
            </Button>
            <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={() => setOfferDropFromNext(false)} className="rounded-md px-2.5 py-1.5 text-xs">
              Keep
            </Button>
          </div>
        )}

        {/* Waiting-on (delegated) */}
        {item.waitingOn && (
          <section>
            <SectionLabel icon={themedIcon("Clock")}>Waiting on</SectionLabel>
            <span className="inline-flex items-center gap-2 text-sm text-foreground">
              <Avatar name={item.waitingOn.name} lg />
              {item.waitingOn.name}
              {item.delegatedAt && (
                <span className="text-xs text-muted-foreground">
                  · since {relativeTime(item.delegatedAt)}
                </span>
              )}
            </span>
            {/* The one place a real promise is stated. Empty is the normal
                state — nobody promised anything, and the Waiting-For overdue
                line reads this task's own due date live (lib/waiting.ts). Set
                it only when they actually committed to a date; clearing it
                takes the promise back rather than writing a second deadline. */}
            <div className="mt-2 max-w-xs">
              <MetaEdit label="Promised by" icon={themedIcon("CalendarClock")}
                display={item.expectedBy
                  ? <span className={`inline-flex items-center gap-1 ${promiseLate ? "font-medium text-destructive" : ""}`}>
                      {promiseLate ? <AppIcon name="AlertTriangle" className="h-3 w-3" /> : <AppIcon name="Clock" className="h-3 w-3" />}
                      {relativeTime(item.expectedBy)}
                    </span>
                  : <span className="text-sm text-muted-foreground/70">
                      {item.dueAt
                        ? `not promised — judged on due ${relativeTime(item.dueAt)}`
                        : "not promised"}
                    </span>}
              >
                {(close) => (
                  <div className="flex items-center gap-1.5">
                    <input
                      type="date"
                      defaultValue={promisedValue}
                      autoFocus
                      onChange={(e) => {
                        updateItem(item.id, {
                          expectedBy: e.target.value
                            ? new Date(e.target.value).toISOString() : "",
                        });
                        close();
                      }}
                      className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                    />
                    {item.expectedBy && (
                      <button type="button" onClick={() => { updateItem(item.id, { expectedBy: "" }); close(); }}
                        className="tech-transition rounded p-1 text-muted-foreground hover:text-destructive" title="Clear the promise">
                        <AppIcon name="X" className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                )}
              </MetaEdit>
            </div>
          </section>
        )}

        {/* Notes — editable */}
        <section>
          <SectionLabel>Notes</SectionLabel>
          <EditableText
            value={item.notes ?? ""}
            placeholder="Add notes, links, context…"
            emptyHint="Add notes"
            multiline
            onSave={(v) => updateItem(item.id, { notes: v })}
          />
        </section>

        {/* Subtasks — editable local children (add / complete). A SYNCED
            parent's subtasks push to ClickUp on the next push. Keyed by id so
            it remounts (and re-fetches) cleanly when switching tasks. */}
        <LocalSubtasksSection key={item.id} item={item} />

        {/* Attachments captured with the item (local) */}
        {item.attachments && item.attachments.length > 0 && (
          <section>
            <SectionLabel>Attachments</SectionLabel>
            <AttachmentChips attachments={item.attachments} />
          </section>
        )}

        {/* Live ClickUp detail: subtasks · comments · attachments */}
        {isSynced && item.providerUrl && (
          <ProviderDetailSections itemId={item.id} provider={item.provider} />
        )}

        {/* Captured-from linkage */}
        {item.origin?.kind === "email" && (
          <section>
            <SectionLabel icon={themedIcon("Mail")}>Captured from</SectionLabel>
            <p className="text-sm text-muted-foreground">
              Email from {item.origin.fromName || item.origin.fromEmail || "someone"}
              {item.origin.subject ? ` — “${item.origin.subject}”` : ""}{"  "}
              <a
                href={originEmailHref(item.origin) ?? "/email"}
                className="tech-transition font-medium text-primary hover:underline"
              >
                Open email
              </a>
            </p>
          </section>
        )}

        {/* The sync badge a row imported before the retirement still carries.
            ⚠️ The `!pushable` guard is gone with the push path (D52, WS-39
            S3a-client slice 4) — nothing is queued at the Action Broker for a
            connector that no longer exists, so the wording below no longer
            promises a write that will never happen. */}
        {sync && backend === "live" && (
          <section className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
            <TaskMeta chips={[sync]} className="text-xs" />
            <p className="mt-1 text-[10px] text-muted-foreground">
              Imported from {item.provider ?? "a connected tool"} before
              workspace sync was retired. Edits stay here.
            </p>
          </section>
        )}

        <p className="mt-1 text-[11px] text-muted-foreground">
          Updated {relativeTime(item.updatedAt)}
          {item.completedAt ? ` · completed ${relativeTime(item.completedAt)}` : ""}
        </p>
      </div>

      {delegateTo && (
        <DelegateDialog
          item={item}
          assignee={delegateTo}
          onClose={() => setDelegateTo(null)}
        />
      )}
    </div>
  );
}

// ── Local subtasks — editable children (add / complete / open) ──────────────

function LocalSubtasksSection({ item }: { item: GtdItem }) {
  const backend = useTaskStore((s) => s.backend);
  const loadSubtasks = useTaskStore((s) => s.loadSubtasks);
  const addSubtasks = useTaskStore((s) => s.addSubtasks);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const openFocus = useTaskStore((s) => s.openFocus);
  // Loading starts true (this section is keyed by item.id at the call site, so
  // it remounts per task — no synchronous setState in the effect to reset it).
  const [subs, setSubs] = useState<GtdItem[]>([]);
  const [loading, setLoading] = useState(backend === "live");
  const [adding, setAdding] = useState("");

  // Load children on open / when the parent's count changes (after an add).
  useEffect(() => {
    if (backend !== "live") return;
    let live = true;
    loadSubtasks(item.id)
      .then((rows) => { if (live) setSubs(rows); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [item.id, item.subtaskCount, backend, loadSubtasks]);

  const add = async () => {
    const t = adding.trim();
    if (!t) return;
    setAdding("");
    const rows = await addSubtasks(item.id, [t]);
    setSubs(rows);
  };

  const toggle = (sub: GtdItem) => {
    const next = sub.disposition === "DONE" ? "NEXT" : "DONE";
    // Optimistic local flip; quickDispose persists the disposition change
    // (and back-syncs a synced child's completion to ClickUp).
    setSubs((cur) =>
      cur.map((s) => (s.id === sub.id ? { ...s, disposition: next } : s)),
    );
    quickDispose(sub.id, next);
  };

  // Demo mode has no server children; hide the section unless the parent
  // already reports some (keeps the mock UI clean).
  if (backend !== "live" && !item.subtaskCount) return null;

  const doneCount = subs.filter((s) => s.disposition === "DONE").length;

  return (
    <section>
      <SectionLabel icon={themedIcon("ListTree")}>
        Subtasks{subs.length > 0 ? ` · ${doneCount}/${subs.length}` : ""}
      </SectionLabel>
      {loading ? (
        <p className="text-[11px] text-muted-foreground">Loading subtasks…</p>
      ) : (
        <div className="flex flex-col gap-1">
          {subs.map((s) => {
            const done = s.disposition === "DONE";
            return (
              <div
                key={s.id}
                className="group flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
              >
                <button
                  type="button"
                  onClick={() => toggle(s)}
                  title={done ? "Mark not done" : "Mark done"}
                  className={[
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors",
                    done
                      ? "border-success bg-success/15 text-success"
                      : "border-border hover:border-primary",
                  ].join(" ")}
                >
                  {done && <AppIcon name="Check" className="h-2.5 w-2.5" />}
                </button>
                <button
                  type="button"
                  onClick={() => openFocus(s.id)}
                  className={[
                    "min-w-0 flex-1 truncate text-left",
                    done ? "text-muted-foreground line-through" : "text-foreground",
                  ].join(" ")}
                >
                  {s.title}
                </button>
                {s.providerUrl && (
                  <a
                    href={s.providerUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 text-muted-foreground/60 hover:text-foreground"
                    title="Open in ClickUp"
                  >
                    <AppIcon name="ExternalLink" className="h-3 w-3" />
                  </a>
                )}
              </div>
            );
          })}
          {backend === "live" && (
            <div className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1.5">
              <AppIcon name="Plus" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <input
                value={adding}
                onChange={(e) => setAdding(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); void add(); }
                }}
                placeholder="Add a subtask…"
                className="min-w-0 flex-1 bg-transparent px-0.5 py-0.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
              {adding.trim() && (
                <Button size="none" radius="keep" layout="" type="button" onClick={() => void add()} className="shrink-0 rounded-md px-2 py-1 text-[11px]">
                  Add
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ── Live provider detail (comments / attachments / subtasks) ────────────────

function ProviderDetailSections({
  itemId,
  provider,
}: {
  itemId: string;
  provider?: string;
}) {
  const [detail, setDetail] = useState<ProviderTaskDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Keyed by item.id upstream, so this component remounts per task and the
  // initial loading=true already covers the reset — the effect only kicks off
  // the fetch and flips state from its async callbacks (no sync setState here).
  useEffect(() => {
    let cancelled = false;
    apiItemDetail(itemId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [itemId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
        Loading {provider === "clickup" ? "ClickUp" : provider} detail…
      </div>
    );
  }
  if (!detail) return null;

  const { comments, subtasks, attachments, error } = detail;
  const nothing =
    !comments.length && !subtasks.length && !attachments.length;

  return (
    <>
      {subtasks.length > 0 && (
        <section>
          <SectionLabel icon={themedIcon("ListTree")}>Subtasks · {subtasks.length}</SectionLabel>
          <div className="flex flex-col gap-1">
            {subtasks.map((s) => <SubtaskRow key={s.providerTaskId} s={s} />)}
          </div>
        </section>
      )}

      {attachments.length > 0 && (
        <section>
          <SectionLabel icon={themedIcon("Paperclip")}>
            Attachments · {attachments.length}
          </SectionLabel>
          <AttachmentChips attachments={attachments} />
        </section>
      )}

      {comments.length > 0 && (
        <section>
          <SectionLabel icon={themedIcon("MessageSquare")}>
            Comments · {comments.length}
          </SectionLabel>
          <div className="flex flex-col gap-2.5">
            {comments.map((c) => <CommentRow key={c.id} c={c} />)}
          </div>
        </section>
      )}

      {error ? (
        <p className="text-[11px] text-muted-foreground">
          {error}. <a
            className="text-primary hover:underline"
            href="#"
            onClick={(e) => { e.preventDefault(); setLoading(true); apiItemDetail(itemId).then(setDetail).finally(() => setLoading(false)); }}
          >Retry</a>
        </p>
      ) : nothing ? (
        <p className="text-[11px] text-muted-foreground">
          No subtasks, comments, or attachments in {provider === "clickup" ? "ClickUp" : "the tool"}.
        </p>
      ) : null}
    </>
  );
}

function SubtaskRow({ s }: { s: TaskSubtask }) {
  const done = s.statusType === "closed" || s.statusType === "done";
  const inner = (
    <div className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
      <span
        className={[
          "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
          done ? "border-success bg-success/15 text-success" : "border-border",
        ].join(" ")}
      >
        {done && <AppIcon name="Check" className="h-2.5 w-2.5" />}
      </span>
      <span className={`min-w-0 flex-1 truncate ${done ? "text-muted-foreground line-through" : "text-foreground"}`}>
        {s.title}
      </span>
      {s.status && (
        <span className="shrink-0 rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {formatStatus(s.status)}
        </span>
      )}
      {s.assignees[0] && <Avatar name={s.assignees[0].name} />}
      {s.providerUrl && <AppIcon name="ExternalLink" className="h-3 w-3 shrink-0 text-muted-foreground/60" />}
    </div>
  );
  return s.providerUrl ? (
    <a href={s.providerUrl} target="_blank" rel="noreferrer" className="tech-transition hover:opacity-80">
      {inner}
    </a>
  ) : inner;
}

function CommentRow({ c }: { c: TaskComment }) {
  return (
    <div className="flex gap-2">
      <Avatar name={c.author} lg />
      <div className="min-w-0 flex-1 rounded-lg rounded-tl-sm border border-border bg-card px-3 py-2">
        <div className="mb-0.5 flex items-center gap-2">
          <span className="text-xs font-semibold text-foreground">{c.author}</span>
          {c.createdAtMs && (
            <span className="text-[10px] text-muted-foreground">
              {relativeTime(new Date(c.createdAtMs).toISOString())}
            </span>
          )}
        </div>
        <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
          {c.text}
        </p>
      </div>
    </div>
  );
}

// ── Small editable building blocks ──────────────────────────────────────────

function SectionLabel({
  icon: Icon,
  children,
}: {
  icon?: ThemedIcon;
  children: React.ReactNode;
}) {
  return (
    <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
      {Icon && <Icon className="h-3 w-3" />}
      {children}
    </h3>
  );
}

function EditableTitle({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const startEdit = () => { setDraft(value); setEditing(true); };

  if (!editing) {
    return (
      <button
        type="button"
        onClick={startEdit}
        className="group flex w-full items-start gap-2 text-left"
        title="Click to edit"
      >
        <h1 className="text-lg font-bold leading-snug text-foreground">{value}</h1>
        <AppIcon name="Pencil" className="mt-1.5 h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </button>
    );
  }
  const save = () => {
    const t = draft.trim();
    if (t) onSave(t);
    setEditing(false);
  };
  return (
    <textarea
      autoFocus
      value={draft}
      rows={2}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
        if (e.key === "Escape") { setDraft(value); setEditing(false); }
      }}
      className="w-full resize-none rounded-md border border-primary/40 bg-background px-2 py-1 text-lg font-bold leading-snug text-foreground focus:outline-none"
    />
  );
}

function EditableText({
  value,
  placeholder,
  emptyHint,
  multiline,
  onSave,
}: {
  value: string;
  placeholder: string;
  emptyHint: string;
  multiline?: boolean;
  onSave: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const startEdit = () => { setDraft(value); setEditing(true); };

  if (!editing) {
    return value ? (
      <button
        type="button"
        onClick={startEdit}
        className="group flex w-full items-start gap-2 rounded-md text-left"
        title="Click to edit"
      >
        <p className={`flex-1 text-sm text-foreground ${multiline ? "whitespace-pre-wrap" : ""}`}>
          {value}
        </p>
        <AppIcon name="Pencil" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </button>
    ) : (
      <button
        type="button"
        onClick={startEdit}
        className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-2.5 py-1.5 text-sm text-muted-foreground hover:border-primary/40 hover:text-foreground"
      >
        <AppIcon name="Pencil" className="h-3 w-3" />
        {emptyHint}
      </button>
    );
  }
  const save = () => {
    onSave(draft.trim());
    setEditing(false);
  };
  const common = {
    autoFocus: true,
    value: draft,
    placeholder,
    onBlur: save,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setDraft(e.target.value),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Escape") { setDraft(value); setEditing(false); }
      if (e.key === "Enter" && !multiline) { e.preventDefault(); save(); }
    },
    className:
      "w-full rounded-md border border-primary/40 bg-background px-2.5 py-1.5 text-sm text-foreground focus:outline-none",
  };
  return multiline ? (
    <textarea rows={4} {...common} className={`${common.className} resize-none`} />
  ) : (
    <input {...common} />
  );
}

/** A metadata cell that flips to an inline editor on click. When closed the
 *  WHOLE card is the click target (label, value, and the empty space around
 *  them) — not just a thin strip under the label — so it's an easy, obvious hit
 *  area. Opening reveals the editor with an × to dismiss. */
function MetaEdit({
  label,
  icon: Icon,
  display,
  children,
}: {
  label: string;
  icon: ThemedIcon;
  display: React.ReactNode;
  children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Edit ${label}`}
        className="tech-transition group min-w-0 rounded-md border border-border bg-card px-3 py-2 text-left hover:border-primary/40 hover:bg-secondary/30"
      >
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            <Icon className="h-3 w-3" />
            {label}
          </span>
          <AppIcon name="Pencil" className="h-3 w-3 shrink-0 text-muted-foreground/50 opacity-0 transition-opacity group-hover:opacity-100" />
        </div>
        <div className="mt-0.5 text-sm text-foreground">
          {display ?? <span className="text-muted-foreground/60">—</span>}
        </div>
      </button>
    );
  }
  return (
    <div className="min-w-0 rounded-md border border-primary/40 bg-card px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          <Icon className="h-3 w-3" />
          {label}
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="tech-transition rounded p-0.5 text-muted-foreground/60 hover:text-foreground"
          aria-label={`Close ${label} editor`}
        >
          <AppIcon name="X" className="h-3 w-3" />
        </button>
      </div>
      <div className="mt-1.5">{children(() => setOpen(false))}</div>
    </div>
  );
}

function ChipMenu({
  options,
  active,
  mono,
  capitalize,
  allowClear,
  format,
  onPick,
}: {
  options: string[];
  active?: string;
  mono?: boolean;
  capitalize?: boolean;
  allowClear?: boolean;
  /** display transform for the chip label; the raw option is still the value
   *  passed to onPick (so e.g. a title-cased status keeps its raw value). */
  format?: (o: string) => React.ReactNode;
  onPick: (v: string | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onPick(o)}
          className={[
            "tech-transition rounded-full border px-2 py-0.5 text-xs",
            mono ? "font-mono" : capitalize ? "capitalize" : "",
            active === o
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:bg-secondary",
          ].join(" ")}
        >
          {format ? format(o) : o}
        </button>
      ))}
      {allowClear && active && (
        <button
          type="button"
          onClick={() => onPick(null)}
          className="tech-transition rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-destructive/40 hover:text-destructive"
        >
          Clear
        </button>
      )}
    </div>
  );
}

/** Same person across the members list and the assignee set — by provider id
 *  when both have one, else by name. */
function samePerson(a: Person, b: Person): boolean {
  if (a.providerUserId && b.providerUserId)
    return a.providerUserId === b.providerUserId;
  return a.name === b.name;
}

/** Overlapping avatars + a label ("Name" for one, "N people" for several). */
function AssigneeStack({ people }: { people: Person[] }) {
  const shown = people.slice(0, 3);
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="flex -space-x-1.5">
        {shown.map((p, i) => (
          <span key={p.providerUserId || p.name || i} className="ring-1 ring-card rounded-full">
            <Avatar name={p.name} />
          </span>
        ))}
        {people.length > shown.length && (
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-secondary text-[8px] font-bold text-muted-foreground ring-1 ring-card">
            +{people.length - shown.length}
          </span>
        )}
      </span>
      <span className="truncate">
        {people.length === 1 ? people[0].name : `${people.length} people`}
      </span>
    </span>
  );
}

/** Multi-select owner picker — each person toggles in/out of the set; the menu
 *  stays open so several can be chosen before dismissing. "Unassigned" clears. */
function MultiPersonMenu({
  people,
  active,
  onToggle,
  onClear,
}: {
  people: Person[];
  active: Person[];
  onToggle: (p: Person) => void;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={onClear}
        className={[
          "tech-transition rounded-full border px-2 py-0.5 text-xs",
          active.length === 0
            ? "border-primary bg-primary/10 text-primary"
            : "border-border text-muted-foreground hover:bg-secondary",
        ].join(" ")}
      >
        Unassigned
      </button>
      {people.map((p) => {
        const on = active.some((a) => samePerson(a, p));
        return (
          <button
            key={p.providerUserId || p.name}
            type="button"
            onClick={() => onToggle(p)}
            aria-pressed={on}
            className={[
              "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs",
              on
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:bg-secondary",
            ].join(" ")}
          >
            {on ? <AppIcon name="Check" className="h-3 w-3 shrink-0" /> : <Avatar name={p.name} />}
            {p.name}
          </button>
        );
      })}
    </div>
  );
}

function PersonMenu({
  people,
  active,
  onPick,
}: {
  people: Person[];
  active: Person | null;
  onPick: (p: Person | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={() => onPick(null)}
        className={[
          "tech-transition rounded-full border px-2 py-0.5 text-xs",
          !active ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
        ].join(" ")}
      >
        Unassigned
      </button>
      {people.map((p) => (
        <button
          key={p.name}
          type="button"
          onClick={() => onPick(p)}
          className={[
            "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs",
            active?.name === p.name ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
          ].join(" ")}
        >
          <Avatar name={p.name} />
          {p.name}
        </button>
      ))}
    </div>
  );
}

/** Status/disposition picker in the header — flips to a small menu. */
function StatusPicker({
  item,
  onPick,
}: {
  item: GtdItem;
  onPick: (d: Disposition) => void;
}) {
  const [open, setOpen] = useState(false);
  const OPTIONS: Disposition[] = ["NEXT", "WAITING", "SOMEDAY", "REFERENCE", "DONE"];
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={[
          "tech-transition inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
          DISP_TONE[item.disposition],
        ].join(" ")}
      >
        {DISPOSITION_LABEL[item.disposition]}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-50 mt-1 w-40 rounded-lg border border-border bg-popover p-1 shadow-xl">
            {OPTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => { onPick(d); setOpen(false); }}
                className={[
                  "tech-transition flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                  item.disposition === d ? "bg-primary/10 text-primary" : "text-foreground hover:bg-secondary",
                ].join(" ")}
              >
                {item.disposition === d && <AppIcon name="Check" className="h-3.5 w-3.5" />}
                <span className={item.disposition === d ? "" : "ml-[22px]"}>
                  {DISPOSITION_LABEL[d]}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Avatar({ name, lg }: { name: string; lg?: boolean }) {
  return (
    <span
      className={[
        "flex items-center justify-center rounded-full bg-primary/15 font-bold text-primary",
        lg ? "h-6 w-6 text-[10px]" : "h-4 w-4 text-[8px]",
      ].join(" ")}
    >
      {initials(name)}
    </span>
  );
}
