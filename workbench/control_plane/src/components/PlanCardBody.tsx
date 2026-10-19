"use client";

/**
 * The body of the plan card: read first, edit on click (owner, 2026-10-18).
 *
 * The owner said the old card "feels intimidating and the UI looks poor". It
 * drew a full form for every task: a Task field, then Owner, Effort (min),
 * Start and Due fields. The owner approved nine changes, and this file draws
 * them:
 *
 * 1. Each task is one line of text. A click on the line, or on its pencil,
 *    opens that one row for editing. Enter opens it from the keyboard, and
 *    focus goes back to the line when it closes.
 * 2. The line holds the tick, the title, the owner chip, the effort in hours
 *    or days, and the dates as short day names.
 * 3. A summary line at the top. The project name in it is the title, and a
 *    click renames it.
 * 4. A timeline strip under the summary: one bar per ticked task.
 * 5. Phases fold, when the rows carry a `phase`.
 * 6. A mark is a small badge. The fit and the hours show when a row opens.
 * 7. "Edit several": pick rows, then set one owner or move their dates.
 * 8. One footer: "Create 7 tasks", counting the ticks, and Cancel.
 * 9. The chat card's look, and a stacked card per task in a narrow column.
 *
 * ⚠️ **The submit does not change.** It is `planSubmit` over the ticked rows,
 * with the same "<label> — <json>" answer, so the server, the confirmation
 * card and the receipt see what the old card sent (golden test in
 * `app/projects/lib/planCard.test.ts`). The decisions live in that module,
 * because the node-env suite cannot render this file's clicks.
 * `e2e/plan-card.spec.ts` drives the clicks in a real browser.
 */

import { useId, useRef, useState } from "react";

import {
  EFFORT_UNITS,
  type EffortUnit,
  type PlanRow,
  type PlanTimeline,
  barInset,
  UNASSIGNED_LABEL,
  PLAN_CANCEL,
  afterCount,
  CYCLE_BADGE,
  afterChoices,
  answeredPlan,
  blankRow,
  createLabel,
  effortIn,
  effortLabel,
  effortMins,
  effortUnit,
  footerNote,
  isUnassigned,
  isMarked,
  keptRows,
  markLine,
  ownerLabel,
  ownerSummary,
  planDay,
  planFacts,
  planGroups,
  planBlocked,
  planCycle,
  planOwners,
  planRowsFrom,
  planSubmitTicked,
  planSummary,
  planTimeline,
  priorityLine,
  rowBadges,
  rowProblem,
  rowSpan,
  summaryParts,
  toggled,
  waitsOnName,
  wholeDays,
  withAfter,
  withOwner,
  withShift,
} from "@/app/projects/lib/planCard";
import { isDeclinedAnswer } from "@/lib/declineAnswer";
import GenUiText from "@/components/GenUiText";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { Input } from "@/components/ui/Input";
import { useOverflowTip } from "@/components/ui/OverflowTip";
import { SelectButton } from "@/components/ui/SelectButton";

type Data = Record<string, unknown>;

const str = (v: unknown, f = ""): string => (typeof v === "string" ? v : v == null ? f : String(v));
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => str(x)).filter(Boolean) : []);

/** The words a sent card shows: the option picker's word. */
const SENT = "Sent";

export default function PlanCardBody({ data, onAction, answered = "", onDecline }: {
  data: Data;
  onAction?: (message: string) => void;
  /** The answer the run already got (`lib/askAnswers.ts`). A reload then
   *  draws the card locked, as the member left it, as the option picker does. */
  answered?: string;
  /** "Not now" of a card that parks a run (`lib/declineAnswer.ts`). With it,
   *  Cancel sends the decline, and the run goes on without a plan. Without
   *  it (a card that parks nothing), Cancel sends "Cancel" as before. */
  onDecline?: () => void;
}) {
  const project = (data.project ?? {}) as Data;
  // One read of the answer, at mount: the card as the member left it.
  const [past] = useState(() => answeredPlan(str(project.name), planRowsFrom(data.tasks), answered));
  const [name, setName] = useState(past?.name ?? str(project.name));
  const [naming, setNaming] = useState(false);
  const [nameBefore, setNameBefore] = useState("");
  const [rows, setRows] = useState<PlanRow[]>(() => past?.rows ?? planRowsFrom(data.tasks));
  const [off, setOff] = useState<Set<string>>(() => past?.off ?? new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [bulk, setBulk] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [bulkOwner, setBulkOwner] = useState("");
  const [bulkDays, setBulkDays] = useState("");
  const [clicked, setDone] = useState<"" | "sent" | "cancelled">("");
  // An answer that lands after mount locks the card too.
  const done = clicked || past?.done
    || (answered ? (answered === PLAN_CANCEL || isDeclinedAnswer(answered) ? "cancelled" : "sent") : "");
  const root = useRef<HTMLDivElement | null>(null);
  const ownersList = useId();

  const year = new Date().getFullYear();
  const kept = keptRows(rows, off);
  const blocked = planBlocked(name, kept);
  const cycle = planCycle(kept);
  const days = wholeDays(bulkDays);
  const groups = planGroups(rows);
  const timeline = planTimeline(kept, year);
  const parts = summaryParts(planSummary(kept), year);
  const owners = ownerSummary(kept);
  const risks = list(data.risks);
  const warnings = list(data.warnings);
  const capacity = str(data.capacity);
  const locked = done !== "";

  /** Focus a control of this card after the render that draws it. */
  const focusLater = (selector: string) =>
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(selector)?.focus());

  const setCell = (key: string, field: keyof PlanRow, v: unknown) =>
    setRows((p) => p.map((r) => (r.key === key ? { ...r, [field]: v } : r)));
  const openRow = (key: string) => {
    if (locked) return;
    setOpen(key);
  };
  const closeRow = (key: string) => {
    setOpen(null);
    focusLater(`[data-plan-line="${CSS.escape(key)}"]`);
  };
  const add = () => {
    const row = blankRow(rows);
    setRows((p) => [...p, row]);
    setOpen(row.key);
  };
  const submit = () => {
    if (!onAction || locked || blocked) return;
    setDone("sent");
    setOpen(null);
    setBulk(false);
    const label = str(data.submitLabel ?? data.title, "Plan");
    onAction(`${label} — ${JSON.stringify(planSubmitTicked(project, name, rows, off))}`);
  };
  const cancel = () => {
    if ((!onAction && !onDecline) || locked) return;
    setDone("cancelled");
    setOpen(null);
    setBulk(false);
    if (onDecline) onDecline();
    else onAction?.(PLAN_CANCEL);
  };
  const endNaming = () => {
    setNaming(false);
    focusLater("[data-plan-name]");
  };

  const drawRow = (r: PlanRow) =>
    open === r.key && !locked ? (
      <RowEditor
        key={r.key}
        row={r}
        rows={rows}
        kept={kept}
        ticked={!off.has(r.key)}
        ownersList={ownersList}
        onTick={(on) => setOff((s) => toggled(s, r.key, !on))}
        onCell={(field, v) => setCell(r.key, field, v)}
        onClose={() => closeRow(r.key)}
      />
    ) : (
      <RowLine
        key={r.key}
        row={r}
        year={year}
        ticked={!off.has(r.key)}
        locked={locked}
        bulk={bulk}
        picked={picked.has(r.key)}
        cyclic={cycle.has(r.key)}
        onTick={(on) => setOff((s) => toggled(s, r.key, !on))}
        onOpen={() => (bulk ? setPicked((s) => toggled(s, r.key, !s.has(r.key))) : openRow(r.key))}
        onEdit={() => openRow(r.key)}
      />
    );

  return (
    <div ref={root} className="flex min-w-0 flex-col gap-2">
      {/* 3 · the summary line: the name is the title, then the facts. */}
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5" data-plan-summary="">
        {naming && !locked ? (
          <Input
            inputSize="sm"
            value={name}
            autoFocus
            aria-label="Project name"
            className="min-w-0 flex-1 basis-48"
            onChange={(e) => setName(e.target.value)}
            onBlur={endNaming}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                // Escape puts the name back as it was before the edit.
                if (e.key === "Escape") setName(nameBefore);
                endNaming();
              }
            }}
          />
        ) : (
          <Button
            variant="text"
            size="none"
            layout="inline-flex min-w-0 items-center gap-1 text-left"
            className="rounded px-0.5 text-sm"
            disabled={locked}
            data-plan-name=""
            aria-label={`Project name: ${name.trim() || "none"}. Rename`}
            onClick={() => { setNameBefore(name); setNaming(true); }}
          >
            <span className="min-w-0 break-words font-semibold text-foreground">{name.trim() || "Name the project"}</span>
            {!locked && <Icon name="Pencil" size={12} className="shrink-0" />}
          </Button>
        )}
        {/* One unit, so a narrow column wraps it under the name whole. */}
        <span className="text-[11px] tabular-nums text-muted-foreground" data-plan-facts-line="">
          {parts.join(" · ")}
        </span>
      </div>
      {data.description != null && str(data.description) && (
        <div className="-mt-1.5 text-[11px] text-muted-foreground"><GenUiText text={str(data.description)} /></div>
      )}

      {/* 4 · the timeline strip (owner report, 2026-10-19: `planTimeline`
          says when it draws). The list is the source of truth and carries
          every date in words, so the strip is hidden from a screen reader. */}
      {timeline && <TimelineStrip timeline={timeline} year={year} />}

      {/* 7 · bulk edits. */}
      {!locked && rows.length > 1 && (
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="text-[11px] text-muted-foreground" data-plan-ticked="">
            {kept.length} of {rows.length} ticked
          </span>
          <Button variant="ghost" size="sm" icon="ListChecks" selected={bulk}
            onClick={() => { setBulk((b) => !b); setPicked(new Set()); setOpen(null); }}>
            Edit several
          </Button>
        </div>
      )}
      {bulk && !locked && (
        <div
          role="group"
          aria-label="Edit several tasks"
          data-plan-bulk=""
          className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-lg border border-border bg-secondary/40 px-2 py-1.5 text-[11px] text-muted-foreground"
        >
          <span aria-live="polite" className="text-foreground">
            {picked.size === 0 ? "Click the tasks to pick them." : `${picked.size} picked`}
          </span>
          <span className="flex items-center gap-1">
            <Input inputSize="sm" className="w-36" value={bulkOwner} list={ownersList}
              placeholder={UNASSIGNED_LABEL} aria-label="Owner for the picked tasks"
              onChange={(e) => setBulkOwner(e.target.value)} />
            <Button variant="secondary" size="sm" disabled={picked.size === 0}
              onClick={() => setRows((p) => withOwner(p, picked, bulkOwner.trim()))}>
              Set owner
            </Button>
          </span>
          <span className="flex items-center gap-1">
            <Input inputSize="sm" type="number" step={1} inputMode="numeric" className="w-16" value={bulkDays}
              aria-label="Days to move the picked tasks by" placeholder="0"
              onChange={(e) => setBulkDays(e.target.value)} />
            <span>days</span>
            <Button variant="secondary" size="sm" disabled={picked.size === 0 || !days}
              onClick={() => { if (days) setRows((p) => withShift(p, picked, days)); }}>
              Move dates
            </Button>
          </span>
          <Button variant="ghost" size="sm" className="ml-auto"
            onClick={() => { setBulk(false); setPicked(new Set()); }}>
            Done
          </Button>
        </div>
      )}
      <datalist id={ownersList}>
        {planOwners(rows).map((o) => <option key={o} value={o} />)}
      </datalist>

      {/* 1, 2, 5 · the rows, under their phases when they carry one. */}
      {groups ? (
        <div className="flex flex-col gap-1">
          {groups.map((g) => (
            <CollapsibleSection key={g.key} label={g.phase} count={g.rows.length}>
              <ul className="flex flex-col gap-1">{g.rows.map(drawRow)}</ul>
            </CollapsibleSection>
          ))}
        </div>
      ) : (
        <ul className="flex flex-col gap-1">{rows.map(drawRow)}</ul>
      )}
      {!locked && (
        <div>
          <Button variant="ghost" size="sm" icon="Plus" onClick={add}>Add a task</Button>
        </div>
      )}

      {(owners || capacity) && (
        <div className="flex flex-col gap-0.5 text-[11px] text-muted-foreground">
          {owners && <span data-plan-owners="">{owners}</span>}
          {capacity && <span>{capacity}</span>}
        </div>
      )}
      {warnings.length > 0 && (
        <CollapsibleSection label="Order warnings" count={warnings.length}>
          <ul className="flex flex-col gap-1 text-xs text-foreground">
            {warnings.map((w, i) => (
              <li key={i} className="flex min-w-0 items-start gap-1.5">
                <Icon name="TriangleAlert" size={12} className="mt-0.5 shrink-0 text-warning" />
                <span className="min-w-0 break-words"><GenUiText text={w} /></span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}
      {risks.length > 0 && (
        <CollapsibleSection label="How this plan fails" count={risks.length} defaultOpen={false}>
          <ul className="ml-4 list-disc space-y-0.5 text-xs text-foreground marker:text-muted-foreground">
            {risks.map((r, i) => <li key={i}><GenUiText text={r} /></li>)}
          </ul>
        </CollapsibleSection>
      )}

      {/* 8 · one footer. */}
      {done ? (
        <div role="status" className="flex flex-wrap items-center gap-2" data-plan-done={done}>
          {done === "sent"
            ? <Badge tone="success" icon="Check">{SENT}</Badge>
            : <Badge tone="neutral" icon="X">Cancelled</Badge>}
          <span className="text-[11px] text-muted-foreground">
            {done === "sent" ? "A confirmation card follows." : "Nothing was created."}
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" icon="Check" onClick={submit} disabled={!onAction || !!blocked} data-plan-submit="">
              {createLabel(kept.length)}
            </Button>
            <Button variant="secondary" size="sm" onClick={cancel} disabled={!onAction && !onDecline}>Cancel</Button>
          </div>
          <span className="text-[11px] text-muted-foreground" data-plan-note="">
            {blocked && kept.length > 0 ? `${blocked} ` : ""}{footerNote(kept.length)}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * The timeline strip: one row per dated task, in the list's order. A bar
 * spans the start to the due date. A faint bar works back from the due
 * date by the effort, and a dot is a due date alone. A small marker at a
 * bar's end is a mark. Hover a row to read its task and dates.
 */
export function TimelineStrip({ timeline, year }: { timeline: PlanTimeline; year: number }) {
  const [hover, setHover] = useState<string | null>(null);
  const bar = timeline.bars.find((b) => b.key === hover);
  const faint = timeline.bars.some((b) => b.derived);
  const inset = barInset(timeline.rowPx);
  // Short, so it fits beside the two dates at 390 px. A touch screen has
  // no hover, and the list under the strip carries every date in words.
  const hint = faint ? "Faint bar: no start date, length from effort" : "";
  return (
    <div data-plan-timeline="" aria-hidden="true" onPointerLeave={() => setHover(null)}>
      <div
        className="relative rounded-md bg-secondary/60 px-1.5 py-1"
        style={{ height: timeline.bars.length * timeline.rowPx + 8 }}
      >
        {timeline.weeks.map((w) => (
          <div key={w} className="absolute inset-y-1 w-px bg-border" style={{ left: `calc(0.375rem + (100% - 0.75rem) * ${w})` }} />
        ))}
        {timeline.bars.map((b, i) => (
          <div
            key={b.key}
            data-plan-bar={b.key}
            data-plan-bar-kind={b.point ? "point" : b.derived ? "derived" : "span"}
            className={`absolute inset-x-1.5 ${hover === b.key ? "bg-foreground/5" : ""}`}
            style={{ top: 4 + i * timeline.rowPx, height: timeline.rowPx }}
            onPointerEnter={() => setHover(b.key)}
          >
            {b.point ? (
              <span
                className="absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-muted-foreground"
                style={{ left: `${b.left * 100}%` }}
              />
            ) : (
              <span
                className={`absolute rounded-full ${b.derived ? "bg-muted-foreground/35" : "bg-muted-foreground/80"}`}
                style={{ top: inset, bottom: inset, left: `${b.left * 100}%`, width: `max(${b.width * 100}%, 3px)` }}
              />
            )}
            {b.marked && (
              <span
                data-plan-bar-mark=""
                className="absolute top-1/2 ml-0.5 size-1 -translate-y-1/2 rounded-full bg-warning"
                style={{ left: `${(b.left + b.width) * 100}%` }}
              />
            )}
          </div>
        ))}
      </div>
      <div className="mt-0.5 flex min-w-0 items-baseline justify-between gap-2 text-[10px] tabular-nums text-muted-foreground">
        <span className="shrink-0">{planDay(timeline.from, year)}</span>
        <span className="min-w-0 truncate text-center" data-plan-bar-caption="">
          {bar ? <><span className="text-foreground">{bar.title}</span> · {bar.dates}</> : hint}
        </span>
        <span className="shrink-0">{planDay(timeline.to, year)}</span>
      </div>
    </div>
  );
}

/** One task, read first: the tick, the line that opens it, and the pencil. */
export function RowLine({ row, year, ticked, locked, bulk, picked, cyclic = false, onTick, onOpen, onEdit }: {
  row: PlanRow;
  year: number;
  ticked: boolean;
  locked: boolean;
  bulk: boolean;
  picked: boolean;
  /** The row sits on a circle of Waits on links among the ticked rows. */
  cyclic?: boolean;
  onTick: (on: boolean) => void;
  onOpen: () => void;
  onEdit: () => void;
}) {
  const title = row.title.trim() || "Untitled task";
  const nobody = isUnassigned(row);
  const effort = effortLabel(row.effort_mins);
  const span = rowSpan(row, year);
  const badges = rowBadges(row);
  const problem = !ticked ? "" : rowProblem(row) || (cyclic ? CYCLE_BADGE : "");
  const who = nobody ? UNASSIGNED_LABEL : ownerLabel(row.owner);
  const said = [who, effort, span, ...badges.map((b) => b.label), problem].filter(Boolean).join(", ");
  const { attachLabel, onPointerEnter, onPointerLeave, tip } = useOverflowTip(title);
  return (
    <li
      data-plan-row={row.key}
      data-plan-off={ticked ? undefined : ""}
      className={`group/row flex min-w-0 items-start gap-2 rounded-lg border px-2 py-1.5 @[34rem]:rounded-md @[34rem]:border-transparent @[34rem]:px-1 @[34rem]:py-1 ${
        picked ? "border-primary/40 bg-primary/10" : "border-border"
      }`}
    >
      <Checkbox
        size="sm"
        className="mt-[3px]"
        checked={ticked}
        disabled={locked}
        aria-label={`Create ${title}`}
        onChange={(e) => onTick(e.target.checked)}
      />
      <Button
        variant="text"
        size="none"
        layout="flex min-w-0 flex-1 text-left"
        className="rounded"
        disabled={locked}
        data-plan-line={row.key}
        aria-label={`${bulk ? "Pick" : "Edit"} ${title}: ${said}`}
        aria-pressed={bulk ? picked : undefined}
        onClick={onOpen}
      >
        {/* The title and its badges take the free width. The owner, the
            effort and the dates have fixed widths in a wide column, so
            they line up as columns down the plan. */}
        <span className={`flex min-w-0 flex-1 flex-col gap-1 @[34rem]:flex-row @[34rem]:items-center @[34rem]:gap-3 ${ticked ? "" : "opacity-50"}`}>
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-1 @[34rem]:flex-nowrap">
            {/* Cut to one line in a wide column, with the whole title in the
                house tip (`OverflowTip`, rule 12). It wraps in a narrow one. */}
            <span ref={attachLabel} onPointerEnter={onPointerEnter} onPointerLeave={onPointerLeave}
              className="min-w-0 break-words text-xs text-foreground @[34rem]:truncate">
              {title}
            </span>
            {tip}
            {badges.map((b) => (
              <Badge key={b.label} tone={b.tone} icon="TriangleAlert" title={markLine(row)} className="shrink-0 font-normal">
                {b.label}
              </Badge>
            ))}
            {problem && <Badge tone="destructive" icon="CircleAlert" className="shrink-0 font-normal">{problem}</Badge>}
          </span>
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-normal text-muted-foreground @[34rem]:shrink-0 @[34rem]:flex-nowrap">
            <span className="@[34rem]:w-28">
              <span
                title={nobody ? undefined : row.owner}
                data-plan-unassigned={nobody ? "" : undefined}
                className={`inline-flex max-w-[7rem] items-center gap-1 rounded-md px-1.5 py-0.5 ${nobody ? "border border-dashed border-border" : "bg-secondary text-foreground"}`}
              >
                <Icon name={nobody ? "UserX" : "User"} size={11} className="shrink-0" />
                <span className="truncate">{who}</span>
              </span>
            </span>
            <span className="tabular-nums @[34rem]:w-10 @[34rem]:text-right">{effort}</span>
            {span && <span className="tabular-nums @[34rem]:w-28">{span}</span>}
          </span>
        </span>
      </Button>
      {!locked && !bulk && (
        <Button
          variant="ghost"
          size="icon-xs"
          icon="Pencil"
          tabIndex={-1}
          aria-label={`Edit ${title}`}
          className={`shrink-0 @[34rem]:opacity-0 @[34rem]:group-hover/row:opacity-100`}
          onClick={onEdit}
        />
      )}
    </li>
  );
}

const FIELD_LABEL = "flex min-w-0 flex-col gap-0.5 text-[11px] text-muted-foreground";

/** One task, open: its fields, its read-only fit and hours, and Waits on. */
export function RowEditor({ row, rows, kept, ticked, ownersList, onTick, onCell, onClose }: {
  row: PlanRow;
  /** Every row, ticked or not: the cycle check reads them all. */
  rows: PlanRow[];
  kept: PlanRow[];
  ticked: boolean;
  ownersList: string;
  onTick: (on: boolean) => void;
  onCell: (field: keyof PlanRow, v: unknown) => void;
  onClose: () => void;
}) {
  const [unit, setUnit] = useState<EffortUnit>(() => effortUnit(row.effort_mins));
  const [effortText, setEffortText] = useState(() => String(effortIn(row.effort_mins, effortUnit(row.effort_mins))));
  const title = row.title.trim() || "Untitled task";
  const options = afterChoices(row, rows, kept);
  const priority = priorityLine(row);
  const facts = planFacts(row, kept);
  const others = kept.filter((r) => r.key !== row.key).length;
  return (
    <li
      data-plan-row={row.key}
      data-plan-open=""
      className="rounded-lg border border-border bg-secondary/40 p-2"
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        // An open list (the effort unit) takes its own Escape first.
        const at = e.target as HTMLElement;
        if (at.closest('[role="listbox"], [role="option"], [aria-expanded="true"]')) return;
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Checkbox size="sm" checked={ticked} aria-label={`Create ${title}`} onChange={(e) => onTick(e.target.checked)} />
          <Input inputSize="sm" value={row.title} autoFocus aria-label="Task" placeholder="What is the task?"
            className="min-w-0 flex-1" onChange={(e) => onCell("title", e.target.value)} />
        </div>
        <div className={`grid grid-cols-2 gap-2 @[34rem]:grid-cols-4`}>
          <label className={FIELD_LABEL}>
            Owner
            <Input inputSize="sm" value={row.owner} list={ownersList} placeholder={UNASSIGNED_LABEL}
              onChange={(e) => onCell("owner", e.target.value)} />
          </label>
          <div className={FIELD_LABEL}>
            <span id={`effort-${row.key}`}>Effort</span>
            <div className="flex min-w-0 gap-1">
              <Input inputSize="sm" type="number" min={0} step={unit === "m" ? 5 : 0.5} value={effortText}
                aria-labelledby={`effort-${row.key}`} className="min-w-0 flex-1"
                onChange={(e) => {
                  setEffortText(e.target.value);
                  const v = parseFloat(e.target.value);
                  onCell("effort_mins", effortMins(Number.isFinite(v) ? v : 0, unit));
                }} />
              <SelectButton
                label="Effort unit"
                value={unit}
                defaultValue={unit}
                widthClass="w-20 shrink-0"
                options={EFFORT_UNITS.map((u) => ({ value: u.unit, label: u.label }))}
                onChange={(v) => {
                  const next = v as EffortUnit;
                  setUnit(next);
                  setEffortText(String(effortIn(row.effort_mins, next)));
                }}
              />
            </div>
          </div>
          <label className={FIELD_LABEL}>
            Start
            <Input inputSize="sm" type="date" value={row.start} onChange={(e) => onCell("start", e.target.value)} />
          </label>
          <label className={FIELD_LABEL}>
            Due
            <Input inputSize="sm" type="date" value={row.due} onChange={(e) => onCell("due", e.target.value)} />
          </label>
        </div>
        {priority && (
          <div className="text-[11px] tabular-nums text-muted-foreground" title="Priority score, a sorting aid" data-plan-priority="">
            {priority}
          </div>
        )}
        {facts.length > 0 && (
          <div className="break-words text-[11px] text-muted-foreground" data-plan-facts="">{facts.join(" · ")}</div>
        )}
        {isMarked(row) && (
          <div className="flex items-start gap-1.5 rounded-md bg-warning/10 px-2 py-1 text-[11px] text-foreground">
            <Icon name="TriangleAlert" size={12} className="mt-px shrink-0 text-warning" />
            <span className="min-w-0 break-words">{markLine(row)}</span>
          </div>
        )}
        {/* WS-27bm S10: what this row waits on. The list leaves out every
            row that waits on this one, so a tick cannot make a cycle. */}
        {options.length === 0 && others > 0 && (
          <div className="text-[11px] text-muted-foreground">Waits on: none. Every other task waits on this one.</div>
        )}
        {options.length > 0 && (
          <CollapsibleSection label="Waits on" count={afterCount(row, kept)} defaultOpen={false} ariaLabel={waitsOnName(row)}>
            <div className="flex flex-col gap-1">
              {options.map((o) => (
                <label key={o.key} className="flex min-w-0 items-center gap-2 text-xs text-foreground">
                  <Checkbox size="sm" checked={row.after.includes(o.key)}
                    onChange={(e) => onCell("after", withAfter(row, kept, o.key, e.target.checked))} />
                  <span className="break-words">{o.title.trim() || "Untitled task"}</span>
                </label>
              ))}
            </div>
          </CollapsibleSection>
        )}
        <div className="flex justify-end">
          <Button variant="secondary" size="sm" onClick={onClose}>Done</Button>
        </div>
      </div>
    </li>
  );
}
