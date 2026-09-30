"use client";

/**
 * Projects · "Import from ClickUp" — the file-import wizard (WS-41 I-4).
 *
 * Spec: `project-docs/specs/project_import.md` §7.7 · decision D80.
 *
 * Five steps, and only the last one writes:
 *
 * 1. **Upload** — the admin chooses the ClickUp workspace export. The server
 *    reads it and plans a dry run. Nothing lands in Projects.
 * 2. **Review** — the counts, the warnings and what the file cannot carry,
 *    before any write. A run that continues an earlier import says so, and
 *    says how many tasks it will UPDATE rather than create (§6.9).
 * 3. **Spaces** (I-8) — where it goes, who can see a new space, and the tree:
 *    leave out a Space, Folder or List, or rename it.
 * 4. **Map** — people, status stages and names (two names merge), and the
 *    columns the importer does not read (I-8). Saving re-plans on the server,
 *    so the numbers on screen are always the server's.
 * 5. **Import** — the writer runs in the background. The dialog polls the run
 *    and shows the report. Closing the dialog does not stop the import.
 *
 * The client computes no count (`importFlow.ts`): the server plans, the
 * wizard shows the plan and collects choices.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import ProgressBar from "@/components/ui/ProgressBar";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import { CATEGORY_HINT, CATEGORY_LABEL, EDITABLE_CATEGORIES } from "@/lib/statusCategory";

import { type ProjectRow, projectsApi } from "../lib/api";
import { importApi } from "../lib/importApi";
import ImportHistory, { DiscardImportButton, type DiscardOutcome, DiscardNotice } from "./ImportHistory";
import ImportTree from "./ImportTree";
import {
  type ColumnChoice,
  type ContainerChoice,
  GRANT_ORG,
  grantOptions,
  stageClashes,
  statusMerges,
  importTreeRows,
  treeTotals,
  type ImportMapping,
  type ImportRun,
  MAX_UPLOAD_BYTES,
  type Stage,
  STEPS,
  type Step,
  isTerminal,
  mappingFrom,
  matchLabel,
  megabytes,
  progressOf,
  continuationNote,
  isStalled,
  keepOnReopen,
  pollDelay,
  mustConfirmNewTree,
  reportLines,
  uploadProblem,
} from "../lib/importFlow";

const UNASSIGNED = "__unassigned__";
const NEW_SPACE = "__new_space__";

interface Props {
  open: boolean;
  onClose: () => void;
  /** The spaces the admin can see, for "import into an existing space". */
  roots: readonly ProjectRow[];
  /** The import finished: refresh the tree, and offer to open a space. */
  onDone: (spaceIds: string[]) => void;
  /** Open one of the spaces the import wrote. */
  onOpenSpace: (spaceId: string) => void;
  /** Open on this earlier run, as Projects settings asks (WS-42). */
  openRunId?: string | null;
}

export default function ImportDialog({ open, onClose, roots, onDone, onOpenSpace, openRunId }: Props) {
  const [step, setStep] = useState<Step>("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [run, setRun] = useState<ImportRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState<Record<string, string | null>>({});
  const [stages, setStages] = useState<Record<string, Stage>>({});
  const [targetId, setTargetId] = useState<string>(NEW_SPACE);
  // The admin has seen that the chosen destination starts a new tree.
  const [confirmedNewTree, setConfirmedNewTree] = useState(false);
  const [spaceName, setSpaceName] = useState("");
  const [members, setMembers] = useState<SelectOption[]>([]);
  // I-8: who can see a new space, the tree's skips and renames, the status
  // names (two alike merge), and what becomes of each unknown column.
  const [grant, setGrant] = useState<string>(GRANT_ORG);
  const [groups, setGroups] = useState<{ slug: string; display_name?: string | null }[]>([]);
  const [containers, setContainers] = useState<Record<string, ContainerChoice>>({});
  const [statusNames, setStatusNames] = useState<Record<string, string>>({});
  const [columns, setColumns] = useState<Record<string, ColumnChoice>>({});
  const picker = useRef<HTMLInputElement>(null);
  const reported = useRef(false);
  // The admin saw the ended run's report while the dialog was open.
  const reportSeen = useRef(false);
  // Bumped on each fresh wizard, so a late answer to an old one is dropped.
  const generation = useRef(0);
  const [misses, setMisses] = useState(0);
  // I-6: what the last discard said, and a key that makes the list read again.
  const [outcome, setOutcome] = useState<DiscardOutcome | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  // When the writer's cursor last moved. Set at the apply, so 0 never counts.
  const lastMove = useRef({ cursor: -1, at: 0 });
  const [stalled, setStalled] = useState(false);

  // A fresh wizard each time it opens, unless a run is still writing or its
  // report is still unseen: the admin may close the dialog mid-run.
  useEffect(() => {
    if (!open) return;
    if (keepOnReopen(run, reportSeen.current)) {
      setStep("run");
      return;
    }
    generation.current += 1;
    reportSeen.current = false;
    setMisses(0);
    setOutcome(null);
    setHistoryKey((k) => k + 1);
    setStep("upload");
    setFiles([]);
    setRun(null);
    setError(null);
    setPeople({});
    setStages({});
    setTargetId(NEW_SPACE);
    setSpaceName("");
    setGrant(GRANT_ORG);
    setContainers({});
    setStatusNames({});
    setColumns({});
    setConfirmedNewTree(false);
    reported.current = false;
    // Only `open` restarts the wizard; the run is read here, not followed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Once the admin has seen an ended run, the next open starts afresh.
  useEffect(() => {
    if (open && step === "run" && isTerminal(run?.state)) reportSeen.current = true;
  }, [open, step, run]);


  // The organization's members, for the people step.
  useEffect(() => {
    if (!open || step !== "map") return;
    let live = true;
    projectsApi
      .suggestAssignees("")
      .then((res) => {
        if (!live) return;
        const rows = Array.isArray(res?.people) ? res.people : [];
        setMembers(rows.map((p) => ({ value: p.assignee.toLowerCase(), label: p.name || p.assignee, hint: p.assignee })));
      })
      .catch(() => live && setMembers([]));
    return () => {
      live = false;
    };
  }, [open, step]);

  // The organization's groups, for "who can see it". An admin who cannot
  // read groups still gets "everyone", which is the default.
  useEffect(() => {
    if (!open || step !== "tree") return;
    let live = true;
    fetch("/api/admin/groups", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : []))
      .then((rows: unknown) => {
        if (live) setGroups(Array.isArray(rows) ? (rows as { slug: string; display_name?: string }[]) : []);
      })
      .catch(() => live && setGroups([]));
    return () => {
      live = false;
    };
  }, [open, step]);

  // Follow the writer until the run ends, with the dialog open or closed, so
  // the tree refreshes when it finishes. A failed poll backs off and goes on.
  useEffect(() => {
    if (step !== "run" || !run || isTerminal(run.state)) return;
    let live = true;
    const timer = setTimeout(() => {
      importApi
        .get(run.id)
        .then((next) => {
          if (!live) return;
          const cursor = next.progress?.cursor ?? 0;
          const at = Date.now();
          if (cursor !== lastMove.current.cursor) lastMove.current = { cursor, at };
          setStalled(isStalled(next, lastMove.current.at, at));
          setMisses(0);
          setError(null);
          setRun(next);
        })
        .catch((err: Error) => {
          if (!live) return;
          setError(`Lost contact with the server (${err.message}). Trying again.`);
          setMisses((n) => n + 1);
        });
    }, pollDelay(misses));
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [step, run, misses]);

  const resume = useCallback(async () => {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      const started = await importApi.apply(run.id);
      lastMove.current = { cursor: -1, at: Date.now() };
      setStalled(false);
      setRun(started);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [run]);

  useEffect(() => {
    if (run?.state === "done" && !reported.current) {
      reported.current = true;
      onDone(run.report?.space_ids ?? []);
    }
  }, [run, onDone]);

  const problem = useMemo(() => uploadProblem(files), [files]);

  // A discard from the list, or from the run it just showed. Either way the
  // tree may have lost spaces, and the list has changed.
  const onDiscardOutcome = useCallback(
    (next: DiscardOutcome) => {
      setOutcome(next);
      if (!next.ok) return;
      setHistoryKey((k) => k + 1);
      // The file is cleared too, so one click cannot import it straight back.
      setFiles([]);
      setRun(null);
      setStep("upload");
      onDone([]);
    },
    [onDone],
  );

  // Open an earlier run from the list: a running one is followed again, and a
  // finished one shows its report.
  const openRun = useCallback(async (runId: string) => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const found = await importApi.get(runId);
      lastMove.current = { cursor: found.progress?.cursor ?? -1, at: Date.now() };
      reported.current = isTerminal(found.state);
      setStalled(false);
      setRun(found);
      setStep("run");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  // Projects settings lists earlier runs; "Open" arrives here with its id.
  useEffect(() => {
    if (!open || !openRunId) return;
    // After the render that opened the dialog, not inside it: the open resets
    // the wizard first, and this then loads the run on top.
    queueMicrotask(() => void openRun(openRunId));
  }, [open, openRunId, openRun]);

  const upload = useCallback(async () => {
    const mine = generation.current;
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      const planned = await importApi.upload(files);
      // The admin closed the dialog and opened a new wizard meanwhile.
      if (mine !== generation.current) return;
      setRun(planned);
      const target = planned.mapping?.target;
      if (target?.kind === "existing" && target.project_id) setTargetId(target.project_id);
      if (target?.name) setSpaceName(target.name);
      setGrant(planned.mapping?.grant || GRANT_ORG);
      setContainers(planned.mapping?.containers ?? {});
      setColumns(planned.mapping?.columns ?? {});
      setStep("review");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [files]);

  const currentMapping = useCallback((): ImportMapping | null => {
    if (!run) return null;
    const target: ImportMapping["target"] =
      targetId === NEW_SPACE
        ? { kind: "new_space", name: spaceName.trim() || null, project_id: null }
        : { kind: "existing", name: null, project_id: targetId };
    return mappingFrom(run, people, stages, target, {
      // An existing space keeps its own sharing.
      grant: targetId === NEW_SPACE ? grant : run.mapping?.grant || GRANT_ORG,
      statusNames,
      containers,
      columns,
    });
  }, [run, people, stages, targetId, spaceName, grant, statusNames, containers, columns]);

  // The Spaces step's Next SAVES and re-plans, so the Map step shows the
  // statuses and people of the tree as chosen. A list ticked back in brings
  // its own statuses, and without this they would import unseen (the I-8
  // review).
  const saveTree = useCallback(async () => {
    const mapping = currentMapping();
    if (!run || !mapping) return;
    setBusy(true);
    setError(null);
    try {
      const planned = await importApi.saveMapping(run.id, mapping);
      setRun(planned);
      if (!planned.plan.ready && planned.plan.errors.length) {
        setError(planned.plan.errors.join(" "));
      }
      setStep("map");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [run, currentMapping]);

  const saveAndImport = useCallback(async () => {
    const mapping = currentMapping();
    if (!run || !mapping) return;
    setBusy(true);
    setError(null);
    try {
      const planned = await importApi.saveMapping(run.id, mapping);
      setRun(planned);
      if (!planned.plan.ready) {
        setError(planned.plan.errors.join(" "));
        return;
      }
      if (mustConfirmNewTree(planned.plan, confirmedNewTree)) {
        // The Map step now shows why; the next "Import" goes ahead.
        setConfirmedNewTree(true);
        return;
      }
      const started = await importApi.apply(run.id);
      lastMove.current = { cursor: -1, at: Date.now() };
      setRun(started);
      setStep("run");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [run, currentMapping, confirmedNewTree]);

  const spaces = roots.filter((r) => !r.parent_project_id);
  const plan = run?.plan;
  const rows = useMemo(() => importTreeRows(plan?.tree ?? [], containers), [plan, containers]);
  const totals = treeTotals(rows);
  const merges = useMemo(() => statusMerges(plan?.statuses ?? [], statusNames), [plan, statusNames]);
  const clashes = useMemo(() => stageClashes(plan?.statuses ?? [], merges, stages), [plan, merges, stages]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import from ClickUp"
      description="Bring a ClickUp workspace export into Projects. The import writes nothing until the last step."
      icon="Upload"
      size="3xl"
    >
      <div className="flex max-h-[75vh] flex-col gap-3 overflow-y-auto p-3">
        <Stepper step={step} />

        {step === "upload" && (
          <section className="flex flex-col gap-3 text-sm">
            <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
              <li>In ClickUp, open Settings, then Imports / Exports, then Export.</li>
              <li>Choose the spaces to bring over, start the export, and download the CSV.</li>
              <li>A large workspace can fail at ClickUp. Export one space at a time, and import each.</li>
            </ol>
            <input
              ref={picker}
              type="file"
              accept=".csv,text/csv"
              multiple
              className="hidden"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" icon="Upload" onClick={() => picker.current?.click()}>
                Choose the export
              </Button>
              <span className="text-xs text-muted-foreground">
                CSV, up to {megabytes(MAX_UPLOAD_BYTES)} in total
              </span>
            </div>
            {files.length > 0 && (
              <ul className="space-y-1 text-xs">
                {files.map((f) => (
                  <li key={f.name} className="flex items-center gap-2">
                    <Icon name="FileText" className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="truncate">{f.name}</span>
                    <span className="text-muted-foreground">{megabytes(f.size)}</span>
                  </li>
                ))}
              </ul>
            )}
            <ImportHistory refreshKey={historyKey} onOpen={(id) => void openRun(id)} onOutcome={onDiscardOutcome} />
          </section>
        )}

        {step === "review" && plan && <Review run={run} />}

        {step === "tree" && plan && (
          <section className="flex flex-col gap-4 text-sm">
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold">Where it goes</h3>
              <div className="flex flex-wrap items-center gap-2">
                <SelectButton
                  label="Import into"
                  value={targetId}
                  onChange={(v) => {
                    // A new destination needs its own confirmation.
                    setTargetId(v);
                    setConfirmedNewTree(false);
                  }}
                  options={[
                    { value: NEW_SPACE, label: "New spaces, one per ClickUp space" },
                    ...spaces.map((s) => ({ value: s.id, label: `Into ${s.name}` })),
                  ]}
                  widthClass="max-w-xs"
                />
                {targetId === NEW_SPACE && plan.summary.spaces === 1 && (
                  <Input
                    aria-label="Space name"
                    placeholder="Space name (optional)"
                    value={spaceName}
                    onChange={(e) => {
                      setSpaceName(e.target.value);
                      setConfirmedNewTree(false);
                    }}
                    className="max-w-xs"
                  />
                )}
              </div>
              {continuationNote(plan) && (
                <p className="text-xs text-muted-foreground">{continuationNote(plan)}</p>
              )}
            </div>

            {targetId === NEW_SPACE && (
              <div className="flex flex-col gap-2">
                <h3 className="text-xs font-semibold">Who can see it</h3>
                <SelectButton
                  label="Who can see the new spaces"
                  value={grant}
                  onChange={setGrant}
                  options={grantOptions(groups).concat(
                    grant !== GRANT_ORG && !groups.some((g) => `group:${g.slug}` === grant)
                      ? [{ value: grant, label: grant.replace(/^group:/, "") }]
                      : [],
                  )}
                  widthClass="max-w-xs"
                />
                <p className="text-xs text-muted-foreground">
                  You can share a space with more people later, from its menu.
                </p>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold">Spaces and lists</h3>
              <p className="text-xs text-muted-foreground">
                Untick what you do not want. Leaving out a space or folder leaves out everything in it, and a
                subtask goes with its parent task.{" "}
                {targetId === NEW_SPACE
                  ? "A new name applies to what this import creates."
                  : "Into an existing space, each ClickUp space and folder becomes one folder."}
              </p>
              {rows.length > 0 ? (
                <ImportTree
                  rows={rows}
                  onSkip={(ref, skip) =>
                    setContainers((c) => ({ ...c, [ref]: { ...c[ref], skip } }))
                  }
                  onRename={(ref, name) => setContainers((c) => ({ ...c, [ref]: { ...c[ref], name } }))}
                />
              ) : (
                <p className="text-xs text-muted-foreground">Upload the file again to choose lists.</p>
              )}
              <p className="text-xs text-muted-foreground" aria-live="polite">
                {totals.lists} {totals.lists === 1 ? "list" : "lists"} with {totals.tasks.toLocaleString()} tasks
                will be imported
                {totals.skippedLists ? `, and ${totals.skippedLists} left out` : ""}.
              </p>
            </div>
          </section>
        )}

        {step === "map" && plan && (
          <section className="flex flex-col gap-4 text-sm">
            {/* The first Import stops when a saved choice breaks a continuation
                (`mustConfirmNewTree`). The reason must be HERE, beside the button. */}
            {confirmedNewTree && !plan.continues && continuationNote(plan) && (
              <p role="status" className="rounded-md border border-border bg-muted px-2 py-1.5 text-xs text-foreground">
                {continuationNote(plan)} Choose Import again to go ahead.
              </p>
            )}
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold">People</h3>
              <p className="text-xs text-muted-foreground">
                A person with no member stays unassigned, and their name is kept on the task. Nobody is invited or
                notified.
              </p>
              <ul className="divide-y divide-border rounded-md border border-border">
                {plan.people.map((person) => {
                  const value = (person.ref in people ? people[person.ref] : person.member) ?? UNASSIGNED;
                  const options: SelectOption[] = [{ value: UNASSIGNED, label: "Leave unassigned" }, ...members];
                  if (person.member && !options.some((o) => o.value === person.member)) {
                    options.push({ value: person.member, label: person.member });
                  }
                  return (
                    <li key={person.ref} className="flex flex-wrap items-center gap-2 px-2 py-1.5">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium">{person.display_name}</p>
                        <p className="text-[11px] text-muted-foreground">
                          {person.tasks} tasks · {matchLabel(person)}
                        </p>
                      </div>
                      <SelectButton
                        label={`Member for ${person.display_name}`}
                        value={value}
                        options={options}
                        onChange={(next) => setPeople((p) => ({ ...p, [person.ref]: next === UNASSIGNED ? null : next }))}
                        widthClass="max-w-[16rem]"
                      />
                    </li>
                  );
                })}
              </ul>
            </div>

            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold">Statuses</h3>
              <p className="text-xs text-muted-foreground">
                Choose the stage of each ClickUp status. A task in a Done or Cancelled stage is closed. Rename a
                status here, and give two statuses the same name to merge them.
              </p>
              <ul className="divide-y divide-border rounded-md border border-border">
                {plan.statuses.map((status) => (
                  <li key={status.name} className="flex flex-wrap items-center gap-2 px-2 py-1.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium">{status.name}</p>
                      <p className="text-[11px] text-muted-foreground">
                        {status.tasks} tasks in {status.lists} {status.lists === 1 ? "list" : "lists"}
                        {merges[status.name]?.length ? ` · merges with ${merges[status.name].join(", ")}` : ""}
                      </p>
                      {clashes.has(status.name) && (
                        <p className="text-[11px] text-destructive">
                          Merged statuses need one stage. Choose the same stage for each.
                        </p>
                      )}
                    </div>
                    <Input
                      inputSize="sm"
                      aria-label={`Name in Metorite for ${status.name}`}
                      placeholder={status.name}
                      value={statusNames[status.name] ?? (status.becomes !== status.name ? status.becomes : "")}
                      maxLength={64}
                      className="max-w-[10rem]"
                      // The stage never changes by itself: a name typed on the
                      // way to another could move closed tasks to open without
                      // a word (the I-8 review). A clash is shown instead.
                      onChange={(e) => setStatusNames((n) => ({ ...n, [status.name]: e.target.value }))}
                    />
                    <SelectButton
                      label={`Stage for ${status.name}`}
                      value={stages[status.name] ?? status.category}
                      options={EDITABLE_CATEGORIES.map((s) => ({
                        value: s,
                        label: CATEGORY_LABEL[s],
                        hint: CATEGORY_HINT[s],
                      }))}
                      onChange={(next) => setStages((st) => ({ ...st, [status.name]: next as Stage }))}
                      widthClass="max-w-[10rem]"
                    />
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        {step === "map" && plan && (plan.columns?.length ?? 0) > 0 && (
          <section className="flex flex-col gap-2 text-sm">
            <h3 className="text-xs font-semibold">Columns Metorite does not read</h3>
            <p className="text-xs text-muted-foreground">
              The file has columns with no place in Metorite, often ClickUp custom fields. Keep one, and each task
              lists its value at the end of its description.
            </p>
            <ul className="divide-y divide-border rounded-md border border-border">
              {plan.columns!.map((col) => (
                <li key={col.name} className="flex flex-wrap items-center gap-2 px-2 py-1.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{col.name}</p>
                    <p className="truncate text-[11px] text-muted-foreground">
                      {col.tasks ? `${col.tasks} tasks` : "Empty in every task"}
                      {col.samples.length ? ` · for example ${col.samples.join(", ")}` : ""}
                    </p>
                  </div>
                  <SelectButton
                    label={`What to do with ${col.name}`}
                    value={columns[col.name] ?? col.choice}
                    disabled={!col.tasks}
                    onChange={(next) => setColumns((c) => ({ ...c, [col.name]: next as ColumnChoice }))}
                    options={[
                      { value: "skip", label: "Leave out" },
                      { value: "description", label: "Keep in the description" },
                    ]}
                    widthClass="max-w-[13rem]"
                  />
                </li>
              ))}
            </ul>
          </section>
        )}

        {step === "run" && run && <Running run={run} onOpenSpace={onOpenSpace} />}

        {outcome && <DiscardNotice outcome={outcome} />}

        {(error || (step === "upload" && files.length > 0 && problem)) && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
            {error ?? problem}
          </p>
        )}

        <footer className="flex items-center justify-end gap-2 border-t border-border pt-3">
          {step === "review" && (
            <Button variant="ghost" onClick={() => setStep("upload")} disabled={busy}>
              Back
            </Button>
          )}
          {step === "tree" && (
            <Button variant="ghost" onClick={() => setStep("review")} disabled={busy}>
              Back
            </Button>
          )}
          {step === "map" && (
            <Button variant="ghost" onClick={() => setStep("tree")} disabled={busy}>
              Back
            </Button>
          )}
          {step === "upload" && (
            <Button variant="primary" loading={busy} disabled={!!problem || busy} onClick={() => void upload()}>
              Read the file
            </Button>
          )}
          {step === "review" && (
            <Button variant="primary" onClick={() => setStep("tree")}>
              Next
            </Button>
          )}
          {step === "tree" && (
            <Button
              variant="primary"
              disabled={busy || (rows.length > 0 && totals.lists === 0)}
              title={rows.length > 0 && totals.lists === 0 ? "Keep at least one list" : undefined}
              loading={busy}
              onClick={() => void saveTree()}
            >
              Next
            </Button>
          )}
          {step === "map" && (
            <Button variant="primary" icon="Download" loading={busy} disabled={busy} onClick={() => void saveAndImport()}>
              Import
            </Button>
          )}
          {step === "run" && run?.discardable && (
            <DiscardImportButton runId={run.id} onOutcome={onDiscardOutcome} />
          )}
          {step === "run" && stalled && run?.state === "applying" && (
            <Button variant="primary" icon="RefreshCw" loading={busy} disabled={busy} onClick={() => void resume()}>
              Resume
            </Button>
          )}
          {step === "run" && (
            <Button variant={isTerminal(run?.state) ? "primary" : "secondary"} onClick={onClose}>
              {isTerminal(run?.state) ? "Close" : "Close — the import keeps going"}
            </Button>
          )}
        </footer>
      </div>
    </Modal>
  );
}

function Stepper({ step }: { step: Step }) {
  const at = STEPS.findIndex((s) => s.id === step);
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]" aria-label="Import steps">
      {STEPS.map((s, i) => (
        <li
          key={s.id}
          aria-current={i === at ? "step" : undefined}
          className={`rounded-md px-2 py-0.5 ${i === at ? "bg-primary/10 text-primary" : "text-muted-foreground"}`}
        >
          {i + 1}. {s.label}
        </li>
      ))}
    </ol>
  );
}

function Review({ run }: { run: ImportRun | null }) {
  const plan = run?.plan;
  if (!plan) return null;
  const counts: [string, number][] = [
    ["Tasks", plan.summary.tasks],
    ["Subtasks", plan.summary.subtasks],
    ["Spaces", plan.summary.spaces],
    ["Folders", plan.summary.folders],
    ["Lists", plan.summary.projects],
    ["Comments", plan.summary.comments],
    ["People", plan.summary.people],
  ];
  return (
    <section className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {counts.map(([label, n]) => (
          <div key={label} className="rounded-md border border-border bg-card px-2 py-1.5">
            <dt className="text-[11px] text-muted-foreground">{label}</dt>
            <dd className="text-sm font-semibold tabular-nums">{n.toLocaleString()}</dd>
          </div>
        ))}
      </dl>
      {continuationNote(plan) && (
        <p className="rounded-md border border-primary/30 bg-primary/10 px-2 py-1.5 text-xs text-primary">
          {continuationNote(plan)}
        </p>
      )}
      {plan.to_update > 0 && (
        <p className="text-xs">
          <Badge tone="primary">{plan.to_update.toLocaleString()}</Badge> tasks are already in Metorite. Each one takes
          ClickUp&apos;s changes, except where somebody edited it here: that edit is kept.
        </p>
      )}
      {plan.to_write.tasks > 0 && (
        <p className="text-xs">
          <Badge tone="success">{plan.to_write.tasks.toLocaleString()}</Badge> new tasks will be created.
        </p>
      )}
      {plan.completed_at_estimated > 0 && (
        <p className="text-xs text-muted-foreground">
          {plan.completed_at_estimated.toLocaleString()} closed tasks have no completion date in the file. Each gets
          the latest date it carries, marked as an estimate.
        </p>
      )}
      {plan.warnings.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-semibold">Worth knowing</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {plan.warnings.map((w) => (
              <li key={w.code}>
                {w.count.toLocaleString()} {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {plan.losses.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-semibold">Not in this file</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {plan.losses.map((l) => (
              <li key={l.what}>
                <span className="font-medium text-foreground">{l.what}</span>: {l.why}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Running({ run, onOpenSpace }: { run: ImportRun; onOpenSpace: (id: string) => void }) {
  const { done, total, percent } = progressOf(run);
  if (run.state === "failed") {
    return (
      <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
        The import stopped: {run.report?.error ?? "no reason was given"}
      </p>
    );
  }
  const lines = reportLines(run.report, run.plan?.skipped_by_choice);
  return (
    <section className="flex flex-col gap-3 text-sm">
      <ProgressBar
        percent={percent}
        label="Import progress"
        detail={`${done.toLocaleString()} of ${total.toLocaleString()} tasks`}
      />
      {run.state === "done" ? (
        <>
          <ul className="list-disc space-y-0.5 pl-5 text-xs">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            {(run.report?.space_ids ?? []).slice(0, 6).map((id, i) => (
              <Button key={id} variant="secondary" size="sm" icon="FolderOpen" onClick={() => onOpenSpace(id)}>
                Open space {i + 1}
              </Button>
            ))}
          </div>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          Writing in the background. Nobody is notified while it runs. You can close this dialog, and the import
          goes on.
        </p>
      )}
    </section>
  );
}
