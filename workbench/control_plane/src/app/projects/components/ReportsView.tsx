"use client";

/**
 * WS-27bk §9.12.8 — reports, rendered IN THE APP.
 *
 * §9.12.8's order is *"render first, deliver second"*, and the reason it gives
 * for the render is not convenience: *"it is useful alone, and it is how the
 * content gets reviewed before anything sends."* Nobody should be able to
 * schedule a weekly email to their colleagues without first seeing, on screen,
 * exactly what that email will say.
 *
 * ⚠️ **Written in the same PR as nothing else, because wave 4 taught the
 * lesson twice.** `/analytics/stuck`, `/analytics/load` and
 * `/analytics/throughput` all shipped with tests and no surface, and the gap
 * was invisible until somebody went looking. An endpoint with no consumer is
 * not half a feature — it is a feature nobody has.
 *
 * ⚠️ **Nothing here computes.** Every number arrives from
 * `GET /projects/reports/{id}/render`, which re-runs §9.12.7's own SQL. This
 * file chooses words and order. The rule is §9.12.8's own: a report with its
 * own arithmetic is the second set of numbers, and two sets disagree.
 *
 * WS-27bn R1 (`projects_reports.md` §6.2) adds the builder: a scope, a period,
 * a subtree toggle, the sections and a name, with a live preview from
 * `POST /projects/reports/preview`. The preview is a server render too. The
 * builder's choices live in `lib/reportBuilder.ts`, as pure functions.
 *
 * WS-27bn R2 (§6.1) adds the home screen: "Your reports" and the template
 * gallery, "Start from a question". The catalogue comes from
 * `GET /projects/reports/templates`, and this file holds no copy of it.
 */
import { useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import Input from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { accentForHue, statusAccent } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  type FinishedReport,
  type PreviewReportBody,
  type ReportRow,
  type ReportTemplate,
  type RenderedReportBody,
  projectsApi,
  projectsKey,
} from "../lib/api";
import { capacityReportRows } from "../lib/capacity";
import { conflictsReportRows } from "../lib/conflicts";
import {
  type BuilderState,
  MAX_REPORT_NAME,
  REPORT_SECTIONS,
  SAVED_PERIOD,
  WHOLE_ORGANIZATION,
  builderStateFrom,
  builderStateFromTemplate,
  configFor,
  createPayload,
  newBuilderState,
  patchPayload,
  periodKey,
  periodOptions,
  saveRefusal,
  scopeOptions,
  templateLabel,
  toggleSection,
  withPeriod,
  yourReports,
} from "../lib/reportBuilder";
import { ReportFileButtons } from "./ReportFileButtons";

/** Hours as a person reads them. Mirrors `AnalyticsPanels`, deliberately. */
function duration(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "not measured";
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "7 – 13 Sep 2026".
 *
 * ⚠️ Parsed by hand, never through `new Date()`. These are floating calendar
 * dates, and `new Date("2026-01-01")` is midnight UTC — a day west of
 * Greenwich, which would name the wrong week on the reader's screen.
 */
function periodLabel(from: string, to: string): string {
  const [fy, fm, fd] = from.split("-");
  const [ty, tm, td] = to.split("-");
  const tail = `${MONTHS[Number(tm) - 1]} ${ty}`;
  if (fy === ty && fm === tm) return `${Number(fd)} – ${Number(td)} ${tail}`;
  if (fy === ty) {
    return `${Number(fd)} ${MONTHS[Number(fm) - 1]} – ${Number(td)} ${tail}`;
  }
  return `${Number(fd)} ${MONTHS[Number(fm) - 1]} ${fy} – ${Number(td)} ${tail}`;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border pt-3">
      <h4 className="mb-2 text-[11px] font-semibold text-foreground">{title}</h4>
      {children}
    </section>
  );
}

/** One project line: a name, a count, and the cancellations beside it. */
function Row({
  name,
  value,
  aside,
  tone,
  title,
}: {
  name: string;
  value: number;
  aside?: string;
  tone?: string;
  title: string;
}) {
  return (
    <li className="flex items-baseline gap-2 text-[11px]" title={title}>
      <span className="min-w-0 truncate pr-px">{name}</span>
      <span className={`ml-auto font-medium tabular-nums ${tone ?? ""}`}>
        {value}
      </span>
      {aside && (
        <span className="tabular-nums text-muted-foreground">{aside}</span>
      )}
    </li>
  );
}

/**
 * The rendered body — what the email will say, on screen first.
 *
 * Exported so it can be rendered against fixture data without a session. The
 * pane around it needs the API; this part is pure, and the part worth looking
 * at before a report is scheduled to anybody.
 */
export function RenderedBody({
  body,
}: {
  body: RenderedReportBody | PreviewReportBody;
}) {
  const done = statusAccent({ category: "done" });
  const late = statusAccent({ category: "cancelled" });
  const { sections } = body;

  return (
    // ⚠️ A readable MEASURE, not the panel's full width. Photographed
    // 2026-09-17: at 1440 the rows ran the whole pane, so "Mobile App" sat
    // about 1200px from its own number and the eye could not join them. A
    // report body is a document, and a document has a column width.
    <div className="max-w-2xl space-y-3">
      <header>
        <h3 className="text-sm font-semibold">{body.report.name}</h3>
        <p className="text-[11px] text-muted-foreground">
          {periodLabel(body.period_start, body.period_end)} ·{" "}
          {body.report.scope === "portfolio"
            ? "Every space you can see"
            : "This project"}
        </p>
      </header>

      {sections.finished && (
        <Section title="What we finished">
          <p
            className={`mb-1 text-lg font-semibold tabular-nums ${done.text}`}
            title={`${sections.finished.total_completed} tasks reached a done status in this period`}
          >
            {sections.finished.total_completed}
          </p>
          <ul className="space-y-0.5">
            {sections.finished.projects.slice(0, 8).map((p) => (
              <Row
                key={p.project_id}
                name={p.name}
                value={p.completed}
                aside={p.cancelled ? `−${p.cancelled}` : undefined}
                title={
                  `${p.completed} finished in ${p.name}` +
                  (p.cancelled ? `, ${p.cancelled} cancelled` : "")
                }
              />
            ))}
          </ul>
          {sections.finished.total_cancelled > 0 && (
            <p
              className="mt-1 text-[11px] text-muted-foreground"
              // ⚠️ Beside the finished count, never added to it. A team that
              // cancelled nine did not finish nine.
              title="Cancellations are never counted as finished work."
            >
              {sections.finished.total_cancelled} cancelled
            </p>
          )}
        </Section>
      )}

      {sections.throughput && (
        <Section title="How long it took">
          <p
            className="text-[11px]"
            title={
              sections.throughput.median_hours === null
                ? "No task in this period recorded both a start and a finish."
                : `Half of the ${sections.throughput.measured} measured tasks took less than this, from first In progress until done.`
            }
          >
            Median{" "}
            <strong className="tabular-nums">
              {duration(sections.throughput.median_hours)}
            </strong>{" "}
            <span className="text-muted-foreground">
              over {sections.throughput.measured} measured
            </span>
          </p>
        </Section>
      )}

      {sections.stuck && sections.stuck.overdue_total > 0 && (
        <Section title="Overdue">
          <p
            className={`mb-1 text-lg font-semibold tabular-nums ${late.text}`}
            title={`${sections.stuck.overdue_total} open tasks are past their due date`}
          >
            {sections.stuck.overdue_total}
          </p>
          <ul className="space-y-0.5">
            {sections.stuck.overdue.slice(0, 8).map((o) => (
              <Row
                key={o.project_id}
                name={o.name}
                value={o.overdue}
                tone={late.text}
                title={`${o.overdue} open tasks in ${o.name} are past their due date`}
              />
            ))}
          </ul>
        </Section>
      )}

      {sections.load && (
        <Section title="Open work">
          <p
            className="mb-1 text-[11px] text-muted-foreground"
            // ⚠️ The rows sum past this. A task with two assignees sits on
            // both plates, so the total is counted over tasks.
            title="Counted over tasks. A task assigned to two people appears in both rows, so the rows add up to more than this."
          >
            {sections.load.total_tasks} open
          </p>
          <ul className="space-y-0.5">
            {sections.load.people.slice(0, 8).map((p) => (
              <Row
                key={p.assignee ?? "__unassigned"}
                name={p.assignee ?? "Unassigned"}
                value={p.open_tasks}
                aside={p.overdue ? `${p.overdue} late` : undefined}
                title={
                  `${p.open_tasks} open for ${p.assignee ?? "nobody"}` +
                  (p.overdue ? `, ${p.overdue} overdue` : "")
                }
              />
            ))}
          </ul>
        </Section>
      )}

      {/* WS-27bm S7a. Opt-in: only a report that asked for `capacity` has it.
          The rows and the hours are the capacity route's, verbatim. */}
      {sections.capacity && (
        <Section title="Who has the hours">
          <p
            className="mb-1 text-[11px] text-muted-foreground"
            title={`Spare hours cover the next ${sections.capacity.horizon_days} days, across all the work the reader can see.`}
          >
            {sections.capacity.total_tasks} open · next{" "}
            {sections.capacity.horizon_days} days
          </p>
          <ul className="space-y-0.5">
            {capacityReportRows(sections.capacity.people).map((p) => (
              <Row
                key={p.key}
                name={p.name}
                value={p.open}
                aside={p.aside}
                title={p.title}
              />
            ))}
          </ul>
          {!sections.capacity.hr_visible && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Hours need HR read access. An admin can see them.
            </p>
          )}
        </Section>
      )}

      {/* WS-27bm S7c. Opt-in: only a report that asked for `conflicts` has
          it. The rows and the sentences are the conflicts route's, verbatim. */}
      {sections.conflicts && (
        <Section title="Where the plan conflicts">
          <p
            className="mb-1 text-[11px] text-muted-foreground"
            title={`Counted by the server over every row. Dated kinds read the next ${sections.conflicts.horizon_days} days.`}
          >
            {sections.conflicts.total} conflicts
          </p>
          <ul className="space-y-1">
            {conflictsReportRows(sections.conflicts.rows).map((c) => (
              <li key={c.key} className="text-[11px]" title={c.sentence}>
                {/* The dot carries the severity, as on the Analytics panel. */}
                <span
                  className={`mr-1.5 inline-block size-1.5 rounded-full align-middle ${accentForHue(c.hue).dot}`}
                  aria-hidden
                />
                <span className="font-medium text-foreground">{c.label}</span>
                <span className="text-muted-foreground"> · {c.sentence}</span>
              </li>
            ))}
          </ul>
          {!sections.conflicts.hr_visible && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Four kinds need HR read access. An admin can see them.
            </p>
          )}
        </Section>
      )}
    </div>
  );
}

/** How long the builder waits after a change before it asks for a preview. */
const PREVIEW_DELAY_MS = 400;

/** An error as one sentence a member can read. */
function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * WS-27bn R1 — the builder, one sentence of chips (§6.2).
 *
 * ⚠️ **The preview is the server's.** Each change asks
 * `POST /projects/reports/preview` after a short delay. The browser computes
 * no figure, so the preview and the saved render are one computation.
 *
 * ⚠️ **An edit sends the WHOLE config.** PATCH replaces `config` on the
 * server, so a partial one would reset the fields it left out.
 *
 * ⚠️ **The scope of a saved report is fixed.** PATCH takes a name and a
 * config, and no `project_id`. So the scope chip is off while editing.
 */
function ReportBuilder({
  initial,
  editing,
  roots,
  onSaved,
  onCancel,
}: {
  initial: BuilderState;
  /** The saved report under edit, or `null` for a new one. */
  editing: ReportRow | null;
  roots: Parameters<typeof scopeOptions>[0];
  onSaved: (row: ReportRow) => void;
  onCancel: () => void;
}) {
  const [state, setState] = useState<BuilderState>(initial);
  const [preview, setPreview] = useState<PreviewReportBody | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const scopes = useMemo(() => scopeOptions(roots), [roots]);
  const refusal = saveRefusal(state);

  // The name is not part of the key: the header shows the typed name, so a
  // keystroke in the name field asks the server for nothing.
  const previewKey = JSON.stringify({
    project_id: state.projectId,
    config: configFor(state),
  });

  useEffect(() => {
    let off = false;
    const { project_id, config } = JSON.parse(previewKey) as {
      project_id: string | null;
      config: ReturnType<typeof configFor>;
    };
    const timer = setTimeout(() => {
      projectsApi.previewReport({ project_id, name: "", config }).then(
        (body) => {
          if (off) return;
          setPreview(body);
          setPreviewError(null);
        },
        (e) => {
          if (off) return;
          setPreviewError(message(e, "The preview could not be rendered."));
        }
      );
    }, PREVIEW_DELAY_MS);
    return () => {
      off = true;
      clearTimeout(timer);
    };
  }, [previewKey]);

  async function save() {
    if (refusal) return;
    setSaving(true);
    setSaveError(null);
    try {
      const row = editing
        ? await projectsApi.patchReport(editing.id, patchPayload(state))
        : await projectsApi.createReport(createPayload(state));
      onSaved(row);
    } catch (e) {
      setSaveError(message(e, "That report could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  const shownName = state.name.trim() || "Untitled report";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Report on</span>
        <SelectButton
          label="Scope"
          widthClass="w-full sm:w-[16rem]"
          value={state.projectId ?? WHOLE_ORGANIZATION}
          defaultValue={WHOLE_ORGANIZATION}
          options={scopes}
          disabled={editing !== null}
          onChange={(value) =>
            setState((s) => ({
              ...s,
              projectId: value === WHOLE_ORGANIZATION ? null : value,
            }))
          }
        />
        <span className="text-muted-foreground">over</span>
        <SelectButton
          label="Period"
          widthClass="w-full sm:w-[14rem]"
          value={periodKey(state) ?? SAVED_PERIOD}
          defaultValue="last_week"
          options={periodOptions(state)}
          onChange={(value) => setState((s) => withPeriod(s, value))}
        />
      </div>

      {state.template && (
        <p className="text-[11px] text-muted-foreground">
          Started from a template. You can change each choice.
        </p>
      )}

      {editing !== null && (
        <p className="text-[11px] text-muted-foreground">
          The scope of a saved report stays as saved. To report on another
          scope, start a new report.
        </p>
      )}

      {state.projectId !== null && (
        <label className="flex items-center gap-2 text-[11px]">
          <Checkbox
            size="sm"
            checked={state.includeSubtree}
            onChange={(e) =>
              setState((s) => ({ ...s, includeSubtree: e.target.checked }))
            }
          />
          Include the projects under it
        </label>
      )}

      <fieldset>
        <legend className="mb-1 text-[11px] font-semibold text-foreground">
          Sections
        </legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {REPORT_SECTIONS.map((section) => {
            const on = state.sections.includes(section.key);
            return (
              <label
                key={section.key}
                className="flex items-center gap-1.5 text-[11px]"
                title={
                  on && state.sections.length === 1
                    ? "A report needs at least one section."
                    : undefined
                }
              >
                <Checkbox
                  size="sm"
                  checked={on}
                  disabled={on && state.sections.length === 1}
                  onChange={() =>
                    setState((s) => ({
                      ...s,
                      sections: toggleSection(s.sections, section.key),
                    }))
                  }
                />
                {section.label}
              </label>
            );
          })}
        </div>
      </fieldset>

      <label className="block text-[11px]">
        <span className="mb-1 block font-semibold text-foreground">Name</span>
        <Input
          inputSize="sm"
          className="w-full sm:w-[20rem]"
          value={state.name}
          maxLength={MAX_REPORT_NAME}
          onChange={(e) => setState((s) => ({ ...s, name: e.target.value }))}
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          loading={saving}
          disabled={refusal !== null}
          onClick={save}
        >
          {editing ? "Save changes" : "Save report"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        {(refusal || saveError) && (
          <p className="text-[11px] text-destructive" role="alert">
            {saveError ?? refusal}
          </p>
        )}
      </div>

      <section className="border-t border-border pt-3" aria-label="Preview">
        <p className="mb-2 text-[11px] text-muted-foreground">
          Preview. The server computes each number, and nothing is saved.
        </p>
        {previewError ? (
          <p className="text-[11px] text-destructive" role="alert">
            {previewError}
          </p>
        ) : preview ? (
          <RenderedBody
            body={{ ...preview, report: { ...preview.report, name: shownName } }}
          />
        ) : (
          <p className="text-[11px] text-muted-foreground">Rendering…</p>
        )}
      </section>
    </div>
  );
}

/** One template card. A coming-soon card is not a control. */
function TemplateCard({
  template,
  onStart,
}: {
  template: ReportTemplate;
  onStart: (template: ReportTemplate) => void;
}) {
  if (!template.available) {
    return (
      <div
        aria-disabled="true"
        className="h-full rounded-lg border border-dashed border-border p-2 text-muted-foreground"
      >
        <div className="flex items-center gap-2">
          <span className="min-w-0 truncate pr-px text-xs font-medium">
            {template.name}
          </span>
          <Badge size="xs" className="ml-auto shrink-0">
            Coming soon
          </Badge>
        </div>
        <p className="mt-1 text-[11px]">{template.question}</p>
        {template.waits_for && (
          <p className="mt-1 text-[10px]">Waits for: {template.waits_for}</p>
        )}
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onStart(template)}
      className="tech-transition h-full w-full rounded-lg border border-border p-2 text-left hover:bg-muted"
    >
      <span className="flex items-center gap-2">
        <Icon name="FileText" className="h-3 w-3 shrink-0 text-primary" />
        <span className="min-w-0 truncate pr-px text-xs font-medium text-foreground">
          {template.name}
        </span>
      </span>
      <span className="mt-1 block text-[11px] text-muted-foreground">
        {template.question}
      </span>
    </button>
  );
}

/**
 * WS-27bn R2 — the Reports home (§6.1, as narrowed for R2).
 *
 * ⚠️ **No card renders on load.** A card names a report. A click selects it,
 * and the existing render runs then. A home screen that rendered each card
 * would run every report's SQL on each visit.
 *
 * ⚠️ **"Your reports" is the server's `mine`.** The browser does not compare
 * addresses. It filters the rows the server marked.
 */
function ReportsHome({
  rows,
  templates,
  templatesError,
  scopeName,
  onOpen,
  onStart,
}: {
  rows: ReportRow[] | null;
  templates: ReportTemplate[] | undefined;
  templatesError: string | null;
  scopeName: (row: ReportRow) => string;
  onOpen: (id: string) => void;
  onStart: (template: ReportTemplate) => void;
}) {
  const mine = yourReports(rows ?? []);
  const live = (templates ?? []).filter((t) => t.available);

  return (
    <div className="space-y-4">
      <section aria-labelledby="reports-yours">
        <h3
          id="reports-yours"
          className="mb-2 text-xs font-semibold text-foreground"
        >
          Your reports
        </h3>
        {rows === null ? (
          <p className="text-[11px] text-muted-foreground">Loading…</p>
        ) : mine.length === 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span>You have not saved a report yet. Start with:</span>
            {live.map((t) => (
              <Button
                key={t.key}
                variant="secondary"
                size="sm"
                onClick={() => onStart(t)}
              >
                {t.name}
              </Button>
            ))}
          </div>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {mine.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => onOpen(r.id)}
                  className="tech-transition h-full w-full rounded-lg border border-border p-2 text-left hover:bg-muted"
                >
                  <span className="block truncate pr-px text-xs font-medium text-foreground">
                    {r.name}
                  </span>
                  <span className="mt-1 block truncate pr-px text-[11px] text-muted-foreground">
                    {templateLabel(r, templates ?? [])} · {scopeName(r)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="reports-gallery">
        <h3
          id="reports-gallery"
          className="mb-2 text-xs font-semibold text-foreground"
        >
          Start from a question
        </h3>
        {templatesError && templates === undefined ? (
          <p className="text-[11px] text-destructive" role="alert">
            {templatesError}
          </p>
        ) : templates === undefined ? (
          <p className="text-[11px] text-muted-foreground">Loading…</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {templates.map((t) => (
              <li key={t.key}>
                <TemplateCard template={t} onStart={onStart} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** What the right pane shows: a saved render, or the builder. */
type Pane =
  | { kind: "view" }
  | { kind: "new"; initial: BuilderState }
  | { kind: "edit"; row: ReportRow };

export default function ReportsView({
  finished,
}: {
  /** The portfolio's finished roll-up, used to offer a first report. */
  finished: FinishedReport | null;
}) {
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [body, setBody] = useState<RenderedReportBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>({ kind: "view" });
  /** Moves after a save, so the render of an edited report runs again. */
  const [renderRound, setRenderRound] = useState(0);

  // The scope chip lists the tree the caller can see. One read cache, the
  // same key the page uses, so this adds no request on a warm page.
  const tree = useCachedResource(projectsKey("tree"), () => projectsApi.tree());
  const roots = useMemo(() => tree.data?.rows ?? [], [tree.data]);
  const scopes = useMemo(() => scopeOptions(roots), [roots]);

  // WS-27bn R2. The catalogue is the server's, through the one read cache.
  const catalogue = useCachedResource(projectsKey("reports/templates"), () =>
    projectsApi.reportTemplates()
  );
  const templates = catalogue.data?.templates;

  function scopeName(row: ReportRow): string {
    if (row.project_id === null) return "Whole organization";
    return scopes.find((s) => s.value === row.project_id)?.label ?? "Project";
  }

  /** Open the builder from a template. A coming-soon one opens nothing. */
  function start(template: ReportTemplate) {
    const initial = builderStateFromTemplate(template);
    if (initial === null) return;
    setPane({ kind: "new", initial });
  }

  useEffect(() => {
    let off = false;
    projectsApi.reports().then(
      (r) => !off && setRows(r.reports),
      () => !off && setRows([])
    );
    return () => {
      off = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setBody(null);
      return;
    }
    let off = false;
    setBody(null);
    setError(null);
    projectsApi.renderReport(selected).then(
      (r) => !off && setBody(r),
      (e) => !off && setError(e?.message ?? "That report could not be rendered.")
    );
    return () => {
      off = true;
    };
  }, [selected, renderRound]);

  function saved(row: ReportRow) {
    setRows((prev) => [row, ...(prev ?? []).filter((r) => r.id !== row.id)]);
    setPane({ kind: "view" });
    setSelected(row.id);
    setRenderRound((n) => n + 1);
  }

  const selectedRow = rows?.find((r) => r.id === selected) ?? null;

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-semibold">Reports</h2>
        <p className="text-[11px] text-muted-foreground">
          A saved question, answered from the same numbers the dashboard shows.
        </p>
        <Button
          className="ml-auto"
          variant="secondary"
          size="sm"
          icon="Plus"
          disabled={pane.kind === "new"}
          onClick={() => setPane({ kind: "new", initial: newBuilderState() })}
        >
          New report
        </Button>
      </div>

      {error && (
        <p className="mb-3 text-[11px] text-destructive" role="alert">
          {error}
        </p>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
        <aside className="rounded-lg border border-border bg-card p-2">
          {rows === null ? (
            <p className="text-[11px] text-muted-foreground">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              No reports yet.{" "}
              {finished
                ? `There are ${finished.total_completed} finished tasks to report on.`
                : ""}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {rows.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(r.id);
                      setPane({ kind: "view" });
                    }}
                    className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[11px] hover:bg-muted ${
                      selected === r.id && pane.kind === "view"
                        ? "bg-muted font-medium"
                        : ""
                    }`}
                  >
                    <Icon name="FileText" className="h-3 w-3 shrink-0" />
                    <span className="min-w-0 truncate pr-px">{r.name}</span>
                    <span
                      className="ml-auto shrink-0 text-muted-foreground"
                      title={
                        r.scope === "portfolio"
                          ? "Every space you can see"
                          : "One project and what is under it"
                      }
                    >
                      {/* ⚠️ "Project", not "Node". `node` is the table's word
                          for a row in the tree and it reaches no other
                          surface — the nav, the tree and this component's own
                          report body all say "space" and "project". A badge
                          that said "Node" made the reader look up an idea the
                          product does not have. */}
                      {r.scope === "portfolio" ? "All" : "Project"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="min-w-0 rounded-lg border border-border bg-card p-3">
          {pane.kind === "new" ? (
            <ReportBuilder
              key={`new:${pane.initial.template ?? "blank"}`}
              initial={pane.initial}
              editing={null}
              roots={roots}
              onSaved={saved}
              onCancel={() => setPane({ kind: "view" })}
            />
          ) : pane.kind === "edit" ? (
            <ReportBuilder
              key={`edit:${pane.row.id}`}
              initial={builderStateFrom(pane.row)}
              editing={pane.row}
              roots={roots}
              onSaved={saved}
              onCancel={() => setPane({ kind: "view" })}
            />
          ) : !selected ? (
            <ReportsHome
              rows={rows}
              templates={templates}
              templatesError={catalogue.error}
              scopeName={scopeName}
              onOpen={(id) => setSelected(id)}
              onStart={start}
            />
          ) : body === null && !error ? (
            <p className="text-[11px] text-muted-foreground">Rendering…</p>
          ) : body ? (
            <div className="space-y-3">
              {/* WS-27bm S8: the report as a file, beside Edit. Rendered
                  again on the click, so the file carries the numbers of that
                  moment. WS-27bn R2: Home returns to the Reports home. */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <ReportFileButtons reportId={selected} />
                {selectedRow && (
                  <div className="ml-auto flex gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="LayoutGrid"
                      onClick={() => setSelected(null)}
                    >
                      Home
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="Pencil"
                      onClick={() => setPane({ kind: "edit", row: selectedRow })}
                    >
                      Edit
                    </Button>
                  </div>
                )}
              </div>
              <RenderedBody body={body} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
