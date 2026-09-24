"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTaskStore, type ClarifyDecision } from "../lib/taskStore";
import {
  proposeClarification,
  defaultStatus,
  isPersonalTask,
  lensDelegateBlock,
  type ClarifyDisposition,
  type ClarifyProposal,
} from "../lib/clarify";
import { apiClarifyPropose, apiSuggestTitle } from "../lib/api";
import type { ConnectedProvider } from "../lib/mockData";
import { Energy, GtdItem, GtdProject, Person, Target } from "../lib/types";
import { durationLabel, formatStatus, initials, originEmailHref, snoozeOptions } from "../lib/utils";
import { SourceBadge } from "./SourceBadge";
import { AttachmentChips } from "./AttachmentComposer";
import { WherePicker } from "./WherePicker";

// F2 — Clarify, redesigned as SORT → SHAPE.
//
// The old card asked ONE 8-way question ("what is it: Next / Project /
// Delegate / Schedule / ...") that silently forced disposition, size, owner,
// and timing into a single mutually-exclusive pick — a task that was a
// PROJECT, DELEGATED, with a DEADLINE, broken into STEPS had no way to be
// expressed. Sort→Shape splits that into:
//
//   STEP 1 — Sort: the one true single-pick (is this even actionable?)
//            Do now · Actionable · Reference · Someday · Trash
//   STEP 2 — Shape (only when Sort=Actionable): four INDEPENDENT axes that
//            combine freely —
//              Size:  single | subtasks | project
//              Owner: me | delegate → person
//              When:  anytime | a date
//              Where: a Space→Folder→List tree, target level driven by Size
//                     (single/subtasks pick a LIST; project picks a
//                     SPACE or FOLDER the new list is created under)
//
// A vague-title gate: the AI's cognition (owner/context/subtasks) is only as
// good as the title. When the title reads as too unclear, a soft-gate banner
// offers a clearer rewrite; the recommend block dims ("best-effort") until
// accepted, but never blocks — you can also press "Improve" on any title.

type Sort = "do-now" | "actionable" | "reference" | "someday" | "trash";
type Size = "single" | "subtasks" | "project";
type Owner = "me" | "delegate";
type When = "anytime" | "date";

const SORT_META: Record<Sort, { label: string; icon: ThemedIcon; danger?: boolean }> = {
  "do-now": { label: "Do now · 2 min", icon: themedIcon("Zap") },
  actionable: { label: "Actionable", icon: themedIcon("ListChecks") },
  reference: { label: "Reference", icon: themedIcon("FileText") },
  someday: { label: "Someday", icon: themedIcon("Lightbulb") },
  trash: { label: "Trash", icon: themedIcon("Trash2"), danger: true },
};
const SORT_ORDER: Sort[] = ["do-now", "actionable", "reference", "someday", "trash"];

const SIZE_META: Record<Size, { label: string; icon: ThemedIcon }> = {
  single: { label: "Single action", icon: themedIcon("ListChecks") },
  subtasks: { label: "Break into steps", icon: themedIcon("ListTree") },
  project: { label: "Project", icon: themedIcon("FolderKanban") },
};

/** Map a disposition (server proposal / current item state) → the Sort bucket. */
function sortOf(d: ClarifyDisposition): Sort {
  if (d === "DO_NOW") return "do-now";
  if (d === "REFERENCE") return "reference";
  if (d === "SOMEDAY") return "someday";
  if (d === "TRASH") return "trash";
  return "actionable"; // NEXT / PROJECT / WAITING / CALENDAR
}
/** Map a disposition → the Size bucket (only meaningful when Sort=actionable). */
function sizeOf(d: ClarifyDisposition, complexity?: string): Size {
  if (d === "PROJECT") return "project";
  if (complexity === "subtasks") return "subtasks";
  if (complexity === "project") return "project";
  return "single";
}

const short = (s: string, n = 26) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const destEntry = (t: Target, providers: ConnectedProvider[]) =>
  t.source === "LOCAL"
    ? providers.find((p) => p.source === "LOCAL")
    : providers.find((p) =>
        t.accountId ? p.id === t.accountId : p.provider === t.provider,
      );
const providerStatuses = (t: Target, providers: ConnectedProvider[]): string[] =>
  destEntry(t, providers)?.statuses ?? [];

export function ClarifyPanel({
  item,
  reclarify = false,
  onDone,
}: {
  item: GtdItem;
  /** Re-clarifying an already-processed task: seed from its CURRENT state, ask
   *  the server to preserve a SYNCED task's ClickUp binding, and lock the
   *  destination picker so the two-way sync target can't be moved. */
  reclarify?: boolean;
  /** Called after a decision is applied (the reclarify modal closes on this). */
  onDone?: () => void;
}) {
  const clarify = useTaskStore((s) => s.clarify);
  const backend = useTaskStore((s) => s.backend);
  const contexts = useTaskStore((s) => s.contexts);
  const people = useTaskStore((s) => s.people);
  const projects = useTaskStore((s) => s.projects);
  const providers = useTaskStore((s) => s.providers);
  const localHierarchy = useTaskStore((s) => s.localHierarchy);
  // S6b — under the lens the Where picker reads these two, never the tree.
  const areas = useTaskStore((s) => s.areas);
  const personalRootId = useTaskStore((s) => s.personalRootId);
  const createArea = useTaskStore((s) => s.createArea);
  const loadPeople = useTaskStore((s) => s.loadPeople);
  const loadLocalHierarchy = useTaskStore((s) => s.loadLocalHierarchy);
  const createLocalProject = useTaskStore((s) => s.createLocalProject);
  const renameItem = useTaskStore((s) => s.renameItem);
  const mergeIntoExisting = useTaskStore((s) => s.mergeIntoExisting);
  const renameExistingFromCapture = useTaskStore((s) => s.renameExistingFromCapture);
  const fileUnderParent = useTaskStore((s) => s.fileUnderParent);

  // Re-clarify seeds the form from the task's CURRENT clarified state (an edit,
  // not a fresh decision). The item's own target is reconstructed so a SYNCED
  // task opens on its ClickUp destination.
  const currentTarget: Target = useMemo(
    () =>
      item.source === "SYNCED"
        ? { source: "SYNCED", provider: item.provider, accountId: item.accountId }
        : { source: "LOCAL", provider: "local" },
    [item.source, item.provider, item.accountId],
  );
  const localProposal = useMemo(
    () =>
      reclarify
        ? {
            ...proposeClarification(item, people, projects),
            disposition:
              item.disposition === "WAITING"
                ? ("WAITING" as ClarifyDisposition)
                : ("NEXT" as ClarifyDisposition),
            nextAction: item.nextAction || item.title,
            context: item.context ?? undefined,
            energy: item.energy,
            target: currentTarget,
            projectId: item.projectId,
            rationale:
              "Re-clarify — adjust the details or break this into next actions.",
          }
        : proposeClarification(item, people, projects),
    [item, people, projects, reclarify, currentTarget],
  );
  // The proposal shown in the AI block: starts as the instant local heuristic,
  // upgraded by the server's when it arrives (richer people/project knowledge
  // lives behind the gateway).
  const [proposal, setProposal] = useState<ClarifyProposal>(localProposal);
  // True once the user changed ANY field — a late server proposal must never
  // stomp on human edits.
  const dirtyRef = useRef(false);

  // ── Local title state (vague-title gate) ────────────────────────────────
  const [title, setTitle] = useState(item.title);
  const [vagueOpen, setVagueOpen] = useState(false);
  const [suggestedTitle, setSuggestedTitle] = useState<string | undefined>(undefined);
  const [titleBusy, setTitleBusy] = useState(false);
  const titleCleared = useRef(false); // user explicitly dismissed/accepted → don't re-show
  // ── Freeform guidance the user gives the assistant to steer this clarify ──
  // (edit the title inline, and/or describe what the item really is → re-run
  //  clarify with that context so the name/project/steps come out right).
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteBusy, setNoteBusy] = useState(false);
  // Dismiss the "already on ClickUp?" banner to file this capture anyway.
  const [dupDismissed, setDupDismissed] = useState(false);
  // Dismiss the "file into this project" suggestion to choose a place manually.
  const [projectDismissed, setProjectDismissed] = useState(false);
  // Dismiss the "this is a step of an existing task" suggestion.
  const [parentDismissed, setParentDismissed] = useState(false);

  // ── Sort → Shape state ────────────────────────────────────────────────────
  // The manual form is PROGRESSIVE DISCLOSURE: the assistant's proposal already
  // seeds every field below, so while the user agrees with it the form is pure
  // duplication — it stays collapsed behind "Adjust". Re-clarify opens expanded
  // (the user came specifically to edit), and the form force-opens whenever the
  // proposal can't be applied as-is (something's missing — show them what).
  const [adjustOpen, setAdjustOpen] = useState(reclarify);
  const [sort, setSort] = useState<Sort>(sortOf(proposal.disposition));
  const [size, setSize] = useState<Size>(sizeOf(proposal.disposition, proposal.complexity));
  const [owner, setOwner] = useState<Owner>(proposal.suggestedAssignee ? "delegate" : "me");
  const [when, setWhen] = useState<When>("anytime");

  const [nextAction, setNextAction] = useState(proposal.nextAction);
  const [outcome, setOutcome] = useState(proposal.outcome ?? `${item.title} — done`);
  const [context, setContext] = useState(proposal.context ?? "@computer");
  const [energy, setEnergy] = useState<Energy>(proposal.energy ?? "medium");
  // Prioritization flags — AI-prefilled, user confirms (urgent is derived from
  // the due date, so it isn't a toggle here).
  const [important, setImportant] = useState<boolean>(!!proposal.important);
  const [leveraged, setLeveraged] = useState<boolean>(!!proposal.leveraged);
  const [deepWork, setDeepWork] = useState<boolean>(!!proposal.deepWork);
  const [assignee, setAssignee] = useState<Person | null>(proposal.suggestedAssignee ?? null);
  const [dueAt, setDueAt] = useState("");
  const [dest, setDest] = useState<Target>(proposal.target ?? { source: "LOCAL", provider: "local" });
  const [projectId, setProjectId] = useState<string | undefined>(proposal.projectId);
  // The Where-axis TARGET when Size=project: create the new list under this
  // space (and optional folder). Independent of `projectId` (the existing-list
  // pick used for single/subtasks).
  const [targetSpaceId, setTargetSpaceId] = useState<string | undefined>(undefined);
  const [targetFolderId, setTargetFolderId] = useState<string | undefined>(undefined);
  const [newListName, setNewListName] = useState(item.title);
  // Subtasks the user chose to break this task into. Seeded from the
  // assistant's suggestion when it read the task as "needs subtasks".
  const [subtasks, setSubtasks] = useState<string[]>(
    proposal.complexity === "subtasks" || proposal.complexity === "project"
      ? proposal.suggestedSubtasks ?? []
      : [],
  );
  const [status, setStatus] = useState<string | undefined>(
    defaultStatus(proposal.disposition, providerStatuses(proposal.target ?? { source: "LOCAL" }, providers)),
  );

  // ── Server proposal upgrade (live backend only) ──────────────────────
  // The panel opened instantly on the local heuristic; swap in the server's
  // richer proposal when it lands — unless the user already touched the form
  // (dirtyRef, set by any interaction inside the panel root below).
  // Seed the whole form from a proposal. Used by all three paths — the instant
  // local heuristic, the server upgrade below, and a note-driven re-clarify —
  // so they stay identical.
  const seedFromProposal = useCallback(
    (sp: ClarifyProposal) => {
      setProposal(sp);
      setSort(sortOf(sp.disposition));
      setSize(sizeOf(sp.disposition, sp.complexity));
      setOwner(sp.suggestedAssignee ? "delegate" : "me");
      setNextAction(sp.nextAction);
      setOutcome(sp.outcome ?? `${item.title} — done`);
      setContext(sp.context ?? "@computer");
      setEnergy(sp.energy ?? "medium");
      setImportant(!!sp.important);
      setLeveraged(!!sp.leveraged);
      setDeepWork(!!sp.deepWork);
      setAssignee(sp.suggestedAssignee ?? null);
      if (sp.target) setDest(sp.target);
      setProjectId(sp.projectId);
      // A guidance-named destination ("put it in ClickUp under Proposals")
      // arrives as a Where target — pre-select it so a Size=project commit
      // creates the new list right there.
      setTargetSpaceId(sp.targetSpaceId);
      setTargetFolderId(sp.targetFolderId);
      setSubtasks(
        sp.complexity === "subtasks" || sp.complexity === "project"
          ? sp.suggestedSubtasks ?? []
          : [],
      );
      setStatus(
        sp.status ??
          defaultStatus(
            sp.disposition,
            providerStatuses(sp.target ?? { source: "LOCAL" }, providers),
          ),
      );
      if (sp.dueDate) {
        setWhen("date");
        setDueAt(sp.dueDate);
      }
      if (sp.isVague && !titleCleared.current) {
        setVagueOpen(true);
        setSuggestedTitle(sp.suggestedTitle);
      } else if (sp.suggestedTitle) {
        setSuggestedTitle(sp.suggestedTitle);
      }
    },
    // Setters are stable; only item.title / providers are read.
    [item.title, providers],
  );

  useEffect(() => {
    if (backend !== "live") return;
    let cancelled = false;
    apiClarifyPropose(item.id, reclarify)
      .then((sp) => {
        if (cancelled || dirtyRef.current) return;
        seedFromProposal(sp);
      })
      .catch(() => {
        /* server proposal is an upgrade, not a dependency — keep the local one */
      });
    return () => {
      cancelled = true;
    };
    // Per-item effect: the panel remounts per item (key={item.id}), and the
    // upgrade must run exactly once per mount — not re-fire on store churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend, item.id]);

  // Re-run clarify with the user's freeform guidance, then re-seed the form —
  // and adopt a clearer title if the assistant proposes one (giving it context
  // is exactly what should let it fix the name).
  const reclarifyWithNote = useCallback(async () => {
    if (backend !== "live") return;
    const n = note.trim();
    if (!n) return;
    setNoteBusy(true);
    try {
      const sp = await apiClarifyPropose(item.id, reclarify, n);
      dirtyRef.current = true; // this is now the authoritative proposal
      seedFromProposal(sp);
      const better = sp.suggestedTitle?.trim();
      if (better && better.toLowerCase() !== title.trim().toLowerCase()) {
        setTitle(better);
        renameItem(item.id, better);
        titleCleared.current = true;
        setVagueOpen(false);
      }
    } catch {
      /* best-effort — keep the existing proposal */
    } finally {
      setNoteBusy(false);
    }
  }, [backend, item.id, reclarify, note, title, seedFromProposal, renameItem]);

  // Load the local hierarchy once (Where axis, local destination) if it hasn't
  // been fetched yet. This used to be a fallback for "the Projects view
  // normally triggers this"; since that view moved to /projects it is the ONLY
  // trigger, and the same now goes for the roster the Who axis offers — a
  // delegate list that silently stays empty reads as a company with no people.
  useEffect(() => {
    if (backend !== "live") return;
    if (!localHierarchy) void loadLocalHierarchy();
    if (people.length === 0) void loadPeople();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend]);

  const selectedProject = projectId
    ? projects.find((p) => p.id === projectId)
    : undefined;
  const statusesForDest = useMemo(() => providerStatuses(dest, providers), [dest, providers]);
  // The delegate roster is the DIRECTORY, full stop. It used to be the
  // destination workspace's member list when there was one, reconciled
  // against the directory by provider id / email / name — forty lines of
  // matching that existed only because two systems held two rosters. One
  // store, one roster.
  const peopleForDelegate: Person[] = people;

  const projectsForDest = useMemo(
    () =>
      projects.filter(
        (p) =>
          p.status === "ACTIVE" &&
          (dest.source === "LOCAL"
            ? p.source === "LOCAL"
            : dest.accountId
              ? p.accountId === dest.accountId
              : p.provider === dest.provider),
      ),
    [projects, dest],
  );

  const isSynced = dest.source === "SYNCED";
  // A re-clarified SYNCED task is two-way bound to its ClickUp list — the
  // destination (workspace + project) can't move, only the local cognition and
  // the break-down can change. The server flags this; lock the picker.
  const destLocked = reclarify && !!proposal.lockedDestination;

  const chooseSize = (s: Size) => {
    setSize(s);
    // The Where target level changes with size — clear the picks so the tree
    // doesn't show a stale selection for the wrong node type.
    setProjectId(undefined);
    setTargetSpaceId(undefined);
    setTargetFolderId(undefined);
  };

  // ── Build the decision from the current Sort→Shape state ────────────────
  const buildDecision = useCallback((): ClarifyDecision | null => {
    if (sort === "do-now") return { kind: "do-now" };
    if (sort === "reference") return { kind: "reference" };
    if (sort === "trash") return { kind: "trash" };
    if (sort === "someday") return { kind: "someday", dest, projectId, status };

    // Actionable — Shape applies.
    const na = nextAction.trim();
    if (!na) return null;
    const dueIso = when === "date" && dueAt ? new Date(dueAt).toISOString() : undefined;
    const delegateTo = owner === "delegate" ? (assignee ?? undefined) : undefined;
    if (owner === "delegate" && !delegateTo) return null; // must pick someone

    if (size === "project") {
      if (!outcome.trim()) return null;
      return {
        kind: "project",
        outcome: outcome.trim(),
        nextAction: na,
        context,
        energy,
        dest,
        status,
        dueAt: dueIso,
        assignee: delegateTo,
        subtasks: subtasks.length ? subtasks : undefined,
      };
    }
    if (when === "date") {
      return {
        kind: "calendar",
        nextAction: na,
        dueAt: dueIso ?? snoozeOptions()[0].iso,
        context,
        dest,
        // Carried like `next` carries it: under the lens a delegated
        // calendar decision moves INTO this project in the same request.
        projectId,
        status,
        assignee: delegateTo,
      };
    }
    return {
      kind: "next",
      nextAction: na,
      context,
      energy,
      dest,
      projectId,
      status,
      dueAt: dueIso,
      assignee: delegateTo,
      subtasks: size === "subtasks" && subtasks.length ? subtasks : undefined,
    };
  }, [sort, size, owner, when, nextAction, outcome, context, energy, assignee,
      dueAt, dest, projectId, status, subtasks]);

  // A pending "create the project under this space/folder" is applied FIRST
  // (mints a real project), then the decision files into it.
  const [creatingTarget, setCreatingTarget] = useState(false);
  const [createTargetError, setCreateTargetError] = useState<string | null>(null);

  const apply = useCallback(async () => {
    // Size=project with no existing project picked but a Where target chosen:
    // create the new list/local-project under that space/folder first.
    if (sort === "actionable" && size === "project" && !projectId) {
      const name = newListName.trim() || nextAction.trim() || item.title;
      if (targetSpaceId || targetFolderId) {
        setCreatingTarget(true);
        setCreateTargetError(null);
        try {
          await createLocalProject({ outcome: name, spaceId: targetSpaceId, folderId: targetFolderId });
        } catch (err) {
          setCreateTargetError(err instanceof Error ? err.message : "Could not create the project");
          setCreatingTarget(false);
          return;
        }
        setCreatingTarget(false);
      }
    }
    const decision = buildDecision();
    if (decision) {
      // Carry the confirmed matrix flags for actionable outcomes (trash/
      // reference/someday don't get prioritized).
      const weightful =
        decision.kind !== "trash" && decision.kind !== "reference";
      clarify(
        item.id,
        decision,
        weightful ? { important, leveraged, deepWork } : undefined,
      );
      onDone?.();
    }
  }, [sort, size, projectId, newListName, nextAction, item.id, item.title,
      targetSpaceId, targetFolderId,
      createLocalProject, buildDecision, clarify, onDone, important, leveraged,
      deepWork]);

  // Delegating to a connected tool needs a destination list so the teammate
  // can see it there — otherwise the task can't be pushed and would strand
  // locally (the clarify-delegate gap). Require a project (existing or a new
  // one being created) whenever we're handing a synced task off to someone.
  const delegatingToSynced =
    sort === "actionable" && owner === "delegate" && !!assignee && isSynced;
  // Under the lens (one store) the rule is the assign guard's, not a
  // connector's: a colleague cannot be put on a task in my PRIVATE tree, so
  // delegating a task that lives there (every inbox capture does) needs a
  // COMPANY project to move into, in the same request. `projects` is the
  // company's list under the lens, so "is a company project" is membership.
  // `isSynced` is permanently false here, which is why the old gate never
  // fired and every such delegate came back 422 (S6a repair).
  // The rule itself is `lib/clarify.ts::lensDelegateBlock`, pure and fenced
  // by `lensRepair.test.ts`; this is only the wiring.
  const lensBlock = lensDelegateBlock({
    lens: true,
    delegating: sort === "actionable" && owner === "delegate" && !!assignee,
    size,
    projectId,
    itemProjectId: item.projectId,
    companyProjectIds: projects.map((p) => p.id),
  });
  // S6b repair. A task on a company board cannot be filed into an Area, nor
  // become one (D62 refuses both moves into a personal tree). The rule is
  // `lib/clarify.ts::isPersonalTask`, fenced by `areas.test.ts`; this hides
  // the two controls that would otherwise offer a 422.
  const personalTask =
    isPersonalTask(item, personalRootId, areas.map((a) => a.id));
  const delegateIntoPrivateProject = lensBlock === "private-project";
  const needsProjectForDelegate =
    (delegatingToSynced && !projectId && !targetSpaceId) || lensBlock !== null;

  // A Size=project decision IS "make this an Area" (S6b): the
  // gateway's `organize` kind=project mints a child of my root named for the
  // outcome, so there is no space or folder to choose first and the decision
  // is complete the moment the outcome and the first action are.
  const projectDecisionReady = !!buildDecision();
  const canApply =
    sort !== "actionable"
      ? true
      : needsProjectForDelegate
        ? false
        : size === "project"
          ? projectDecisionReady
          : !!buildDecision();

  // Progressive disclosure: show the manual Sort→Shape form only when the user
  // asked to adjust — or when the proposal can't apply as-is, so the missing
  // piece (an assignee, a destination list…) is visible instead of a dead
  // Accept button.
  const showForm = adjustOpen || !canApply;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable))
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Enter" && canApply) {
        e.preventDefault();
        void apply();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [apply, canApply]);

  // ── Title: "Improve" (always available) + the vague-title gate ─────────
  const runSuggestTitle = useCallback(async () => {
    if (backend !== "live") return;
    setTitleBusy(true);
    try {
      const r = await apiSuggestTitle(item.id, title);
      setSuggestedTitle(r.suggestedTitle);
      if (r.suggestedTitle) setVagueOpen(true);
    } catch {
      /* best-effort */
    } finally {
      setTitleBusy(false);
    }
  }, [backend, item.id, title]);

  const acceptTitle = () => {
    if (suggestedTitle) {
      setTitle(suggestedTitle);
      renameItem(item.id, suggestedTitle);
    }
    titleCleared.current = true;
    setVagueOpen(false);
  };
  const keepTitle = () => {
    titleCleared.current = true;
    setVagueOpen(false);
  };

  const trashNow = () => {
    clarify(item.id, { kind: "trash" });
    onDone?.();
  };

  const Meta = SORT_META[sortOf(proposal.disposition)];
  const SizeIcon = SIZE_META[sizeOf(proposal.disposition, proposal.complexity)].icon;
  const proposedDestLabel = proposal.target
    ? destEntry(proposal.target!, providers)?.label
    : undefined;
  const proposedProject = proposal.projectId ? projects.find((p) => p.id === proposal.projectId) : undefined;
  const dimmed = vagueOpen; // best-effort: the recommend block dims until the title is clear
  const bigSuggestion = proposal.complexity === "subtasks" || proposal.complexity === "project";

  return (
    <div
      className="flex h-full flex-col overflow-y-auto"
      // Any click/keystroke inside the panel = the human is editing; a late
      // server proposal must no longer re-seed the form (capture phase so it
      // fires before the child handler mutates state).
      onPointerDownCapture={() => { dirtyRef.current = true; }}
      onKeyDownCapture={() => { dirtyRef.current = true; }}
    >
      <header className="border-b border-border bg-card px-5 py-4">
        <div className="mb-2 flex items-center gap-2">
          <span className="rounded bg-primary/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
            {reclarify ? "Re-clarify" : "Clarify"}
          </span>
          <SourceBadge source={item.source} provider={item.provider} />
        </div>
        <div className="flex items-start gap-2">
          {/* The title is directly editable — retype it, or use the context
              field / Improve to have the assistant rewrite it. Saves on blur /
              Enter; Escape reverts. An auto-growing TEXTAREA (not an input) so
              a long capture WRAPS and stays fully readable — you can't clarify
              what you can't read, especially on mobile. */}
          <textarea
            value={title}
            rows={1}
            ref={(el) => {
              if (el) {
                el.style.height = "auto";
                el.style.height = `${el.scrollHeight}px`;
              }
            }}
            onChange={(e) => {
              setTitle(e.target.value);
              e.currentTarget.style.height = "auto";
              e.currentTarget.style.height = `${e.currentTarget.scrollHeight}px`;
            }}
            onBlur={() => {
              const t = title.trim();
              if (t && t !== item.title) renameItem(item.id, t);
              if (!t) setTitle(item.title); // never allow a blank title
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
              if (e.key === "Escape") { setTitle(item.title); e.currentTarget.blur(); }
            }}
            aria-label="Task title — click to edit"
            className="tech-transition -mx-1 flex-1 resize-none overflow-hidden rounded-md border border-transparent bg-transparent px-1 text-lg font-bold leading-snug text-foreground hover:border-border focus:border-primary/50 focus:bg-background focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void runSuggestTitle()}
            disabled={titleBusy || backend !== "live"}
            title="Rephrase this title with AI"
            className="tech-transition mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/10 disabled:opacity-40"
          >
            {titleBusy ? <AppIcon name="Loader2" className="h-3 w-3 animate-spin" /> : <AppIcon name="Wand2" className="h-3 w-3" />}
            Improve
          </button>
        </div>
        {item.origin?.kind === "email" && (
          <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
            <AppIcon name="Mail" className="h-3 w-3 shrink-0" />
            <span className="truncate">
              Captured from email — {item.origin.fromName || item.origin.fromEmail}
              {item.origin.subject ? ` · “${item.origin.subject}”` : ""}
            </span>
            <a
              href={originEmailHref(item.origin) ?? "/email"}
              className="tech-transition shrink-0 font-medium text-primary hover:underline"
            >
              Open
            </a>
          </p>
        )}
        {item.attachments && item.attachments.length > 0 && (
          <div className="mt-1">
            <AttachmentChips attachments={item.attachments} />
          </div>
        )}
        {/* (No tagline — the assistant block and the Sort field label already
            carry the "what is it?" question; every saved row keeps the dialog
            calmer.) */}
        {/* Freeform guidance → re-clarify. Collapsed by default so it costs no
            height; expand to describe what the item really is and let the
            assistant re-derive the title / project / steps from it. */}
        {backend === "live" && (
          <div className="mt-2">
            {!noteOpen ? (
              <button
                type="button"
                onClick={() => setNoteOpen(true)}
                className="tech-transition inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
              >
                <AppIcon name="Sparkles" className="h-3 w-3" />
                Add context for the assistant
              </button>
            ) : (
              <div className="flex flex-col gap-1.5">
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={2}
                  autoFocus
                  placeholder="Describe what this really is — e.g. “the Q3 board deck; break it into sections and file under Fundraising”. The assistant re-clarifies the title, project and steps from this."
                  className="w-full resize-y rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                />
                <div className="flex items-center gap-2">
                  <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => void reclarifyWithNote()} disabled={noteBusy || !note.trim()} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
                    {noteBusy ? (
                      <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <AppIcon name="Wand2" className="h-3.5 w-3.5" />
                    )}
                    Re-clarify with this
                  </Button>
                  <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={() => { setNoteOpen(false); setNote(""); }} className="rounded-md px-2 py-1.5 text-xs">
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </header>

      <div className="flex flex-col gap-4 px-5 py-4">
        {/* Possible duplicate already on the PM tool — offer to merge into it or
            drop this capture before we file a second copy. Only on a fresh
            inbox clarify (a reclarified task IS the synced task). */}
        {!reclarify && proposal.duplicate && !dupDismissed && (
          <DuplicateBanner
            dup={proposal.duplicate}
            captureTitle={item.title}
            onMerge={async () => {
              await mergeIntoExisting(item.id, proposal.duplicate!.itemId);
              onDone?.();
            }}
            onRename={async (newTitle) => {
              await renameExistingFromCapture(
                item.id, proposal.duplicate!.itemId, newTitle,
              );
              onDone?.();
            }}
            onDrop={() => { clarify(item.id, { kind: "trash" }); onDone?.(); }}
            onDismiss={() => setDupDismissed(true)}
          />
        )}

        {/* Vague-title banner (soft gate) */}
        {vagueOpen && (
          <div className="flex flex-col gap-2 rounded-lg border border-warning/45 bg-warning/10 p-3">
            <div className="flex items-start gap-2 text-sm font-semibold text-foreground">
              <AppIcon name="AlertTriangle" className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <span>
                {proposal.isVague
                  ? "This title is vague — clarify it so the assistant can clarify the rest"
                  : "A clearer title is available"}
              </span>
            </div>
            {proposal.isVague && (
              <p className="text-[11.5px] text-muted-foreground">
                A specific title makes the owner, context, and next steps far more accurate.
              </p>
            )}
            {suggestedTitle && (
              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Assistant suggests
                </span>
                <input
                  value={suggestedTitle}
                  onChange={(e) => setSuggestedTitle(e.target.value)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                />
              </div>
            )}
            <div className="flex items-center gap-2">
              <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={acceptTitle} disabled={!suggestedTitle} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
                <AppIcon name="Check" className="h-3.5 w-3.5" />
                Use this title
              </Button>
              <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={keepTitle} className="rounded-md px-2.5 py-1.5 text-xs">
                Keep original
              </Button>
            </div>
          </div>
        )}

        {/* Sub-step of an existing task — file this as a subtask under it
            rather than a standalone (clarify-only, scoped to the matched
            project). Takes priority over the project suggestion (filing under
            a parent already places it in that project). */}
        {!reclarify && !proposal.duplicate && proposal.parentSuggestion &&
          !parentDismissed && (
          <ParentSuggestBanner
            parentTitle={proposal.parentSuggestion.title}
            onFileUnder={async () => {
              await fileUnderParent(item.id, proposal.parentSuggestion!.itemId);
              onDone?.();
            }}
            onDismiss={() => setParentDismissed(true)}
          />
        )}

        {/* Suggested project — file this into an existing local/ClickUp project
            it logically belongs to (parallel to the duplicate check). Hidden
            when a duplicate is already flagged (that decision comes first) or
            when a parent-task suggestion is active (that files it too). */}
        {!reclarify && !proposal.duplicate && proposal.projectInferred &&
          proposal.projectId && proposedProject && !projectDismissed &&
          (!proposal.parentSuggestion || parentDismissed) && (
          <ProjectSuggestBanner
            project={proposedProject}
            providerLabel={
              proposedProject.source !== "LOCAL" && proposal.target
                ? destEntry(proposal.target, providers)?.label
                : undefined
            }
            assignee={owner === "delegate" ? assignee : null}
            onFile={() => void apply()}
            onDismiss={() => setProjectDismissed(true)}
          />
        )}

        {/* AI proposal — review & confirm. Leads with SIZE + concrete steps. */}
        <div
          className={[
            "relative rounded-lg border border-primary/30 bg-primary/5 p-3 transition-opacity",
            dimmed ? "opacity-60" : "",
          ].join(" ")}
        >
          {dimmed && (
            <span className="absolute -top-2 right-3 rounded bg-card px-1.5 text-[9px] font-semibold uppercase tracking-wide text-warning">
              Improves once the title is clear
            </span>
          )}
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-primary">
              <AppIcon name="Sparkles" className="h-3.5 w-3.5" />
              Assistant recommends
            </div>
            <span
              className={[
                "rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
                proposal.confidence === "high"
                  ? "bg-primary/15 text-primary"
                  : "bg-secondary text-muted-foreground",
              ].join(" ")}
              title={
                proposal.confidence === "high"
                  ? "The assistant is confident — accept in one tap."
                  : "A best guess — glance and confirm, or adjust."
              }
            >
              {proposal.confidence === "high" ? "Confident" : "Best guess"}
            </span>
          </div>
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Meta.icon className="h-4 w-4 text-primary" />
            {Meta.label}
            {Meta.label === "Actionable" && (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                · <SizeIcon className="h-3.5 w-3.5" />
                {SIZE_META[sizeOf(proposal.disposition, proposal.complexity)].label}
              </span>
            )}
            {proposal.suggestedAssignee && (
              <span className="text-muted-foreground">→ {proposal.suggestedAssignee.name}</span>
            )}
          </div>
          {proposal.disposition === "PROJECT" && proposal.outcome && (
            <p className="mt-1.5 text-sm text-muted-foreground">
              Outcome: <span className="text-foreground">{proposal.outcome}</span>
            </p>
          )}
          {sortOf(proposal.disposition) === "actionable" && !bigSuggestion && (
            <p className="mt-1.5 text-sm text-foreground">
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {proposal.disposition === "PROJECT" ? "First action" : "Next action"}
              </span>
              <br />
              {proposal.nextAction}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
            {proposal.context && <span className="font-mono text-primary/80">{proposal.context}</span>}
            {proposal.energy && <span>{proposal.energy} energy</span>}
            {proposal.deepWork && (
              <span
                title="Needs an unbroken flow state — the planner will protect a long peak-energy block"
                className="inline-flex items-center gap-1 rounded-full border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 font-medium text-sky-600 dark:text-sky-400"
              >
                🌊 deep work
              </span>
            )}
            {proposal.timeEstimateMins ? <span>{durationLabel(proposal.timeEstimateMins)}</span> : null}
            {proposal.dueDate && (
              <span className="inline-flex items-center gap-1">
                <AppIcon name="CalendarDays" className="h-3 w-3" /> by {proposal.dueDate}
              </span>
            )}
            {sortOf(proposal.disposition) === "actionable" && proposedDestLabel && (
              <span className="inline-flex items-center gap-1 rounded bg-secondary px-1.5 py-0.5 font-medium">
                {proposal.target?.source === "LOCAL" ? (
                  <AppIcon name="HardDrive" className="h-3 w-3" />
                ) : (
                  <AppIcon name="Cloud" className="h-3 w-3" />
                )}
                {proposedDestLabel}
                {proposedProject ? ` · ${short(proposedProject.outcome, 16)}` : ""}
              </span>
            )}
          </div>

          {/* The assistant's break-down — numbered, editable, created on accept. */}
          {bigSuggestion && (
            <div className="mt-2.5 flex flex-col gap-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Next actions to create
              </span>
              <SubtaskEditor value={subtasks} onChange={setSubtasks} numbered />
            </div>
          )}

          <p className="mt-1.5 text-[11px] italic text-muted-foreground">{proposal.rationale}</p>
          {/* The one decision row: Accept (primary) · Adjust (open the manual
              form) · Trash (escape hatch). Accept disables — instead of
              silently no-opping — when the decision can't build yet; the form
              below force-opens in that case to show what's missing. */}
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => void apply()} disabled={creatingTarget || !canApply} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
              {creatingTarget ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="Check" className="h-3.5 w-3.5" />}
              {bigSuggestion
                ? `Accept & create all ${subtasks.length || 1} step${subtasks.length === 1 ? "" : "s"}`
                : "Accept & next"}
            </Button>
            {!showForm && (
              <button
                type="button"
                onClick={() => setAdjustOpen(true)}
                title="Change the sort, owner, timing, or destination yourself"
                className="tech-transition inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:border-primary/40 hover:text-foreground"
              >
                <AppIcon name="Pencil" className="h-3 w-3" />
                Adjust
              </button>
            )}
            <button
              type="button"
              onClick={trashNow}
              title="Trash this — it's not actionable"
              className="tech-transition ml-auto inline-flex items-center gap-1.5 rounded-md border border-transparent px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:border-destructive/30 hover:bg-destructive/10 hover:text-destructive"
            >
              <AppIcon name="Trash2" className="h-3.5 w-3.5" />
              Trash
            </button>
          </div>
        </div>

        {/* The manual form — collapsed while the proposal is accepted as-is
            (the assistant block above already SHOWS these values); opened via
            Adjust, on re-clarify, or when the decision needs more input. */}
        {showForm && (<>
        {/* STEP 1 — Sort */}
        <Field label="Sort it — what kind of thing is this?">
          <div className="flex flex-wrap gap-1.5">
            {SORT_ORDER.map((s) => {
              const M = SORT_META[s];
              const active = sort === s;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSort(s)}
                  className={[
                    "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                    active
                      ? M.danger
                        ? "border-destructive bg-destructive/10 text-destructive"
                        : "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-secondary",
                  ].join(" ")}
                >
                  <M.icon className="h-3.5 w-3.5" />
                  {M.label}
                </button>
              );
            })}
          </div>
        </Field>

        {/* STEP 2 — Shape (only when Sort=Actionable) */}
        {sort === "actionable" && (
          <div className="flex flex-col gap-3.5 border-t border-border pt-3.5">
            <Field label="Shape it — these combine freely">
              <div className="flex flex-col gap-3">
                <SubField label="Size" inline>
                  <div className="flex flex-wrap gap-1.5">
                    {(["single", "subtasks", "project"] as Size[]).map((s) => {
                      // "Project" mints an AREA under the lens, and a team
                      // task cannot become one (S6b repair, D62).
                      if (s === "project" && !personalTask) return null;
                      const M = SIZE_META[s];
                      const active = size === s;
                      return (
                        <button
                          key={s}
                          type="button"
                          onClick={() => chooseSize(s)}
                          className={[
                            "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                            active ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
                          ].join(" ")}
                        >
                          <M.icon className="h-3.5 w-3.5" />
                          {M.label}
                        </button>
                      );
                    })}
                  </div>
                </SubField>

                {(size === "project" ? "outcome" : "next-action") === "outcome" && (
                  <SubField label="Outcome">
                    <input
                      value={outcome}
                      onChange={(e) => setOutcome(e.target.value)}
                      className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                      placeholder="What does 'done' look like?"
                    />
                  </SubField>
                )}

                <SubField label={size === "project" ? "First next action" : "Next action"}>
                  <input
                    value={nextAction}
                    onChange={(e) => setNextAction(e.target.value)}
                    className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                    placeholder="The next physical, visible step…"
                  />
                </SubField>

                {(size === "subtasks" || size === "project") && (
                  <SubField label="Steps">
                    <SubtaskEditor value={subtasks} onChange={setSubtasks} />
                  </SubField>
                )}

                <SubField label="Owner" inline>
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => setOwner("me")}
                      className={[
                        "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        owner === "me" ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
                      ].join(" ")}
                    >
                      <AppIcon name="User" className="h-3.5 w-3.5" /> Me
                    </button>
                    <button
                      type="button"
                      onClick={() => setOwner("delegate")}
                      className={[
                        "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        owner === "delegate" ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
                      ].join(" ")}
                    >
                      <AppIcon name="UserPlus" className="h-3.5 w-3.5" /> Delegate →
                    </button>
                  </div>
                  {owner === "delegate" && (
                    <div className="mt-2">
                      <PeoplePicker people={peopleForDelegate} value={assignee} onChange={setAssignee} />
                      {proposal.assigneeLoad?.overloaded &&
                        assignee &&
                        proposal.suggestedAssignee &&
                        (assignee.providerUserId
                          ? assignee.providerUserId ===
                            proposal.suggestedAssignee.providerUserId
                          : assignee.name.toLowerCase() ===
                            proposal.suggestedAssignee.name.toLowerCase()) && (
                          <p className="mt-1.5 flex items-start gap-1 text-[11px] text-amber-500">
                            <AppIcon name="AlertTriangle" className="mt-0.5 h-3 w-3 shrink-0" />
                            <span>
                              {assignee.name.split(/\s+/)[0]} is{" "}
                              {proposal.assigneeLoad.note ?? "already at capacity"} —
                              consider spreading the load.
                            </span>
                          </p>
                        )}
                    </div>
                  )}
                </SubField>

                <SubField label="When" inline>
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => setWhen("anytime")}
                      className={[
                        "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        when === "anytime" ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
                      ].join(" ")}
                    >
                      Anytime
                    </button>
                    <button
                      type="button"
                      onClick={() => setWhen("date")}
                      className={[
                        "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        when === "date" ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
                      ].join(" ")}
                    >
                      <AppIcon name="CalendarClock" className="h-3.5 w-3.5" /> By a date →
                    </button>
                  </div>
                  {when === "date" && (
                    <input
                      type="date"
                      value={dueAt}
                      onChange={(e) => setDueAt(e.target.value)}
                      className="mt-2 rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                    />
                  )}
                </SubField>

                <SubField label="Energy" inline>
                  <div className="flex gap-1.5">
                    {(["low", "medium", "high"] as Energy[]).map((e) => (
                      <Pill key={e} active={energy === e} onClick={() => setEnergy(e)}>
                        {e}
                      </Pill>
                    ))}
                  </div>
                </SubField>

                <SubField label="Context" inline>
                  <div className="flex flex-wrap gap-1.5">
                    {contexts.map((c) => (
                      <Pill key={c.name} mono active={context === c.name} onClick={() => setContext(c.name)}>
                        {c.name}
                      </Pill>
                    ))}
                  </div>
                </SubField>

                {/* Your focus — the matrix inputs (AI-prefilled, you confirm).
                    Urgent is derived from the due date, so it isn't a toggle.
                    D77 (F1): never "Priority", which is the shared field. */}
                <SubField label="Your focus" inline>
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap gap-1.5">
                      <Pill active={important} onClick={() => setImportant((v) => !v)}>
                        ❗ Important
                      </Pill>
                      <Pill active={leveraged} onClick={() => setLeveraged((v) => !v)}>
                        ⚖️ Leveraged
                      </Pill>
                      {/* Work MODE, not priority rank: flow-state work gets a
                          protected, unbroken peak-energy block from the planner. */}
                      <Pill active={deepWork} onClick={() => setDeepWork((v) => !v)}>
                        🌊 Deep work
                      </Pill>
                    </div>
                    {proposal.weightReason && (
                      <span className="text-[11px] text-muted-foreground/70">
                        {proposal.weightReason}
                      </span>
                    )}
                  </div>
                </SubField>

                {/* Where — present for EVERY actionable task, single or broken-down.
                    Target level follows Size: list (single/subtasks) vs space/folder (project). */}
                <SubField label="Where">
                  {destLocked ? (
                    <LockedWhere dest={dest} providers={providers} selectedProject={selectedProject}
                      statuses={statusesForDest} status={status} setStatus={setStatus} />
                  ) : (
                    <div className="flex flex-col gap-2.5">
                      {size === "project" ? (
                        /* S6b. Under one store a private project is an AREA —
                           a flat child of my root, named for the outcome, and
                           the gateway mints it from this decision. There is
                           nothing to place it under, so no tree is drawn. */
                        <div className="flex items-center gap-2 rounded-md border border-border bg-background/40 px-3 py-2 text-xs text-muted-foreground">
                          <AppIcon name="FolderPlus" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                          <span>
                            This becomes an Area called{" "}
                            <span className="font-medium text-foreground">
                              {outcome.trim() ? short(outcome.trim(), 40) : "the outcome"}
                            </span>
                            , with the first action inside it.
                          </span>
                        </div>
                      ) : (
                        /* S6b. My Areas, then the company's projects — and
                           never `/tasks/hierarchy` (spec S6b done-when 3). */
                        <WherePicker
                          areas={areas}
                          includeAreas={personalTask}
                          projects={projectsForDest}
                          value={projectId}
                          suggestedId={proposal.projectInferred ? proposal.projectId : undefined}
                          onChange={setProjectId}
                          onCreateArea={createArea}
                        />
                      )}

                      {size === "project" && (targetSpaceId || targetFolderId) && (
                        <SubField label="New project name">
                          <input
                            value={newListName}
                            onChange={(e) => setNewListName(e.target.value)}
                            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                          />
                        </SubField>
                      )}
                      {createTargetError && (
                        <p className="text-[11px] text-destructive">{createTargetError}</p>
                      )}

                      {statusesForDest.length > 0 && (
                        <SubField label="Stage">
                          <div className="flex flex-wrap gap-1.5">
                            {statusesForDest.map((s) => (
                              <Pill key={s} plain active={status === s} onClick={() => setStatus(s)}>
                                {s}
                              </Pill>
                            ))}
                          </div>
                        </SubField>
                      )}
                      {!isSynced && (
                        <p className="text-[10px] text-muted-foreground">
                          Private to you until it joins a company project. File it in an Area, or leave it loose.
                        </p>
                      )}
                    </div>
                  )}
                </SubField>
              </div>
            </Field>

            {needsProjectForDelegate && (
              <p className="inline-flex items-center gap-1 text-[11px] font-medium text-warning">
                <AppIcon name="AlertTriangle" className="h-3 w-3 shrink-0" />
                {delegateIntoPrivateProject
                  ? "A delegated task cannot become a private project. Choose Next and pick a project."
                  : <>Pick a project to delegate into — {assignee?.name.split(/\s+/)[0]}{" "}
                    needs to be able to find it.</>}
              </p>
            )}
          </div>
        )}
        {/* The form's own apply — OUTSIDE the actionable-only block, so picking
            Reference/Someday/Trash in the Sort row also has a visible button
            (previously only actionable had one; other sorts relied on Enter). */}
        <button
          type="button"
          disabled={!canApply || creatingTarget}
          onClick={() => void apply()}
          className={[
            "tech-transition inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2.5 text-sm font-medium",
            canApply && !creatingTarget
              ? "bg-primary text-primary-foreground hover:opacity-90"
              : "cursor-not-allowed bg-secondary text-muted-foreground",
          ].join(" ")}
        >
          {creatingTarget ? <AppIcon name="Loader2" className="h-4 w-4 animate-spin" /> : null}
          Organize it <AppIcon name="ArrowRight" className="h-4 w-4" />
        </button>
        </>)}
      </div>
    </div>
  );
}

// The "belongs in an existing project?" banner shown during inbox processing
// when the assistant infers this capture logically fits a local or ClickUp
// project. One tap files it there as a next action; the outcome line spells out
// where it will surface (mine → My Next Actions; delegated → under that person
// in Projects, and on ClickUp).
function ProjectSuggestBanner({
  project,
  providerLabel,
  assignee,
  onFile,
  onDismiss,
}: {
  project: GtdProject;
  providerLabel?: string;
  assignee: Person | null;
  onFile: () => void;
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const synced = project.source !== "LOCAL";
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-primary/35 bg-primary/5 p-3">
      <div className="flex items-start gap-2 text-sm font-semibold text-foreground">
        <AppIcon name="FolderKanban" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <span>Looks like it belongs in an existing project</span>
      </div>
      <div className="rounded-md border border-border bg-background/50 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[12.5px] text-foreground">
          {synced ? (
            <AppIcon name="Cloud" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
          ) : (
            <AppIcon name="HardDrive" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate" title={project.outcome}>{project.outcome}</span>
        </div>
        <div className="mt-0.5 pl-5 text-[10.5px] text-muted-foreground">
          {synced ? (providerLabel ?? "Project board") : "Local project"}
        </div>
      </div>
      <p className="flex items-start gap-1.5 text-[11.5px] text-muted-foreground">
        <AppIcon name="ArrowRight" className="mt-0.5 h-3 w-3 shrink-0 text-primary/70" />
        {assignee
          ? `Assigned to ${assignee.name} — it'll show under them in Projects${synced ? ", and stays on its board" : ""}.`
          : `Assigned to you — it'll show up in My Next Actions${synced ? ", and on its board" : ""}.`}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="none" radius="keep" layout="inline-flex items-center" type="button" disabled={busy} onClick={() => { setBusy(true); onFile(); }} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
          {busy ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="Check" className="h-3.5 w-3.5" />}
          File it here
        </Button>
        <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={onDismiss} className="ml-auto rounded-md px-2.5 py-1.5 text-xs">
          Choose another place
        </Button>
      </div>
    </div>
  );
}

// The "looks like a step of an existing task" banner shown during inbox
// processing when the assistant infers this capture is a sub-step of a task in
// the matched project. One tap files it as a subtask under that task.
function ParentSuggestBanner({
  parentTitle,
  onFileUnder,
  onDismiss,
}: {
  parentTitle: string;
  onFileUnder: () => Promise<void>;
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-primary/35 bg-primary/5 p-3">
      <div className="flex items-start gap-2 text-sm font-semibold text-foreground">
        <AppIcon name="ListTree" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <span>Looks like a step of an existing task</span>
      </div>
      <div className="rounded-md border border-border bg-background/50 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[12.5px] text-foreground">
          <AppIcon name="FolderKanban" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
          <span className="min-w-0 flex-1 truncate" title={parentTitle}>{parentTitle}</span>
        </div>
      </div>
      <p className="flex items-start gap-1.5 text-[11.5px] text-muted-foreground">
        <AppIcon name="ArrowRight" className="mt-0.5 h-3 w-3 shrink-0 text-primary/70" />
        File it as a subtask under this task instead of a standalone one.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="none" radius="keep" layout="inline-flex items-center" type="button" disabled={busy} onClick={async () => { setBusy(true); try { await onFileUnder(); } finally { setBusy(false); } }} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
          {busy ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="ListTree" className="h-3.5 w-3.5" />}
          File as subtask
        </Button>
        <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={onDismiss} className="ml-auto rounded-md px-2.5 py-1.5 text-xs">
          It&apos;s its own task
        </Button>
      </div>
    </div>
  );
}

// The "already on ClickUp?" banner shown during inbox processing when a
// token-free lexical match finds a likely-existing PM-tool task. Offers four
// exits: fold this capture INTO the existing task (merge), rename the existing
// task to this capture's clearer title (rename — back-syncs for a SYNCED
// target, then drops the capture), drop this capture (it's a duplicate), or
// dismiss and file it anyway (keep both).
function DuplicateBanner({
  dup,
  captureTitle,
  onMerge,
  onRename,
  onDrop,
  onDismiss,
}: {
  dup: NonNullable<ClarifyProposal["duplicate"]>;
  captureTitle: string;
  onMerge: () => Promise<void>;
  onRename: (newTitle: string) => Promise<void>;
  onDrop: () => void;
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState<null | "merge" | "drop" | "rename">(null);
  // Inline rename editor: seeded with the capture's (usually more descriptive)
  // title so one tap makes the existing task clearer.
  const [renaming, setRenaming] = useState(false);
  const [renameTitle, setRenameTitle] = useState(captureTitle);
  const run = async (
    which: "merge" | "drop" | "rename",
    fn: () => void | Promise<void>,
  ) => {
    setBusy(which);
    try { await fn(); } finally { setBusy(null); }
  };
  const canRename = renameTitle.trim().length > 0
    && renameTitle.trim() !== dup.title.trim();
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-warning/45 bg-warning/10 p-3">
      <div className="flex items-start gap-2 text-sm font-semibold text-foreground">
        <AppIcon name="AlertTriangle" className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
        <span>
          {dup.verdict === "duplicate"
            ? "This looks like a task that's already on a project board"
            : "A similar task may already be on a project board"}
        </span>
      </div>
      <div className="rounded-md border border-border bg-background/50 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[12.5px] text-foreground">
          <AppIcon name="Cloud" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
          <span className="min-w-0 flex-1 truncate" title={dup.title}>{dup.title}</span>
          {dup.providerUrl && (
            <a
              href={dup.providerUrl}
              target="_blank"
              rel="noreferrer"
              className="tech-transition shrink-0 text-[11px] font-medium text-primary hover:underline"
            >
              Open
            </a>
          )}
        </div>
        {(dup.projectName || dup.providerStatus) && (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 pl-5 text-[10.5px] text-muted-foreground">
            {dup.projectName && <span>{dup.projectName}</span>}
            {dup.providerStatus && <span>· {formatStatus(dup.providerStatus)}</span>}
          </div>
        )}
      </div>
      {renaming ? (
        // Rename the EXISTING task to a clearer title from this capture, then
        // drop the capture. Back-syncs upstream for a SYNCED target.
        <div className="flex flex-col gap-2 rounded-md border border-primary/30 bg-background/50 p-2.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Rename the existing task to
          </span>
          <input
            value={renameTitle}
            onChange={(e) => setRenameTitle(e.target.value)}
            autoFocus
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
          />
          <p className="text-[10.5px] text-muted-foreground">
            Updates the task everywhere it lives and drops this inbox item.
          </p>
          <div className="flex items-center gap-2">
            <Button size="none" radius="keep" layout="inline-flex items-center" type="button" disabled={busy !== null || !canRename} onClick={() => void run("rename", () => onRename(renameTitle))} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
              {busy === "rename" ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="Check" className="h-3.5 w-3.5" />}
              Save name
            </Button>
            <Button variant="ghost" size="none" radius="keep" layout="" type="button" disabled={busy !== null} onClick={() => { setRenaming(false); setRenameTitle(captureTitle); }} className="rounded-md px-2.5 py-1.5 text-xs">
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="none" radius="keep" layout="inline-flex items-center" type="button" disabled={busy !== null} onClick={() => void run("merge", onMerge)} className="gap-1.5 rounded-md px-2.5 py-1.5 text-xs">
            {busy === "merge" ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="Plus" className="h-3.5 w-3.5" />}
            Add to existing task
          </Button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => setRenaming(true)}
            className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-foreground hover:border-primary/40 hover:bg-primary/10 disabled:opacity-50"
          >
            <AppIcon name="Pencil" className="h-3.5 w-3.5" />
            Update its name
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run("drop", onDrop)}
            className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-transparent px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:border-destructive/30 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
          >
            {busy === "drop" ? <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> : <AppIcon name="Trash2" className="h-3.5 w-3.5" />}
            Delete this inbox item
          </button>
          <Button variant="ghost" size="none" radius="keep" layout="" type="button" disabled={busy !== null} onClick={onDismiss} className="ml-auto rounded-md px-2.5 py-1.5 text-xs">
            Not a duplicate
          </Button>
        </div>
      )}
    </div>
  );
}

function LockedWhere({
  dest, providers, selectedProject, statuses, status, setStatus,
}: {
  dest: Target;
  providers: ConnectedProvider[];
  selectedProject?: GtdProject;
  statuses: string[];
  status?: string;
  setStatus: (s: string) => void;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/40 px-3 py-2">
        <AppIcon name="Lock" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-foreground">
          <AppIcon name="Cloud" className="h-3.5 w-3.5 text-primary/70" />
          {destEntry(dest, providers)?.label ?? "Project board"}
          {selectedProject && (
            <>
              <span className="text-border">·</span>
              <AppIcon name="FolderKanban" className="h-3 w-3 text-muted-foreground" />
              {short(selectedProject.outcome, 22)}
            </>
          )}
        </span>
      </div>
      {statuses.length > 0 && (
        <div className="mt-2">
          <SubField label="Stage">
            <div className="flex flex-wrap gap-1.5">
              {statuses.map((s) => (
                <Pill key={s} plain active={status === s} onClick={() => setStatus(s)}>
                  {s}
                </Pill>
              ))}
            </div>
          </SubField>
        </div>
      )}
      <p className="mt-1.5 text-[10px] text-muted-foreground">
        Bound to its project board — the project stays put. You can
        still change size, owner, timing, stage, and break it into steps.
      </p>
    </div>
  );
}

// A small add/remove list for breaking a task into concrete subtasks. Seeded
// from the assistant's suggestion; the user edits before applying.
function SubtaskEditor({
  value,
  onChange,
  numbered,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  numbered?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim();
    if (!t) return;
    onChange([...value, t]);
    setDraft("");
  };
  const remove = (idx: number) => onChange(value.filter((_, i) => i !== idx));
  const edit = (idx: number, text: string) =>
    onChange(value.map((s, i) => (i === idx ? text : s)));

  return (
    <div className="flex flex-col gap-1.5">
      {value.map((s, idx) => (
        <div key={idx} className="flex items-center gap-1.5">
          {numbered ? (
            <span className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded bg-primary/15 text-[10px] font-bold text-primary">
              {idx + 1}
            </span>
          ) : (
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/50" />
          )}
          <input
            value={s}
            onChange={(e) => edit(idx, e.target.value)}
            onBlur={() => { if (!s.trim()) remove(idx); }}
            className="min-w-0 flex-1 rounded-md border border-border bg-background/60 px-2 py-1.5 text-sm text-foreground focus:border-primary/50 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => remove(idx)}
            aria-label="Remove step"
            className="tech-transition shrink-0 rounded p-1 text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive"
          >
            <AppIcon name="X" className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1.5">
        <AppIcon name="Plus" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(); }
          }}
          placeholder={value.length ? "Add another step…" : "Add a step…"}
          className="min-w-0 flex-1 bg-transparent px-0.5 py-0.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        {draft.trim() && (
          <Button size="none" radius="keep" layout="" type="button" onClick={add} className="shrink-0 rounded-md px-2 py-0.5 text-[11px]">
            Add
          </Button>
        )}
      </div>
    </div>
  );
}

function PeoplePicker({
  people,
  value,
  onChange,
}: {
  people: Person[];
  value: Person | null;
  onChange: (p: Person | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {people.map((p) => (
        <button
          key={p.name}
          type="button"
          onClick={() => onChange(p)}
          className={[
            "tech-transition inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
            value?.name === p.name ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
          ].join(" ")}
        >
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary/15 text-[8px] font-bold text-primary">
            {initials(p.name)}
          </span>
          {p.name}
        </button>
      ))}
      {!people.length && (
        <p className="text-[11px] text-muted-foreground">No teammates available yet.</p>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold text-foreground">{label}</h3>
      {children}
    </div>
  );
}

function SubField({
  label,
  children,
  inline = false,
}: {
  label: string;
  children: React.ReactNode;
  /** Put the label to the LEFT of the control on wider screens (stacks on
   *  narrow). Used for the compact pill rows so each doesn't cost a full
   *  stacked block — keeps the Shape step from running tall. */
  inline?: boolean;
}) {
  if (inline) {
    return (
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
        <p className="shrink-0 text-[10px] font-semibold uppercase leading-tight tracking-wide text-muted-foreground sm:w-16 sm:pt-1.5">
          {label}
        </p>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    );
  }
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {children}
    </div>
  );
}

function Pill({
  active,
  mono,
  plain,
  onClick,
  children,
}: {
  active: boolean;
  mono?: boolean;
  plain?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "tech-transition rounded-full border px-2.5 py-1 text-xs",
        mono ? "font-mono" : plain ? "" : "capitalize",
        active ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-secondary",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
