"use client";

/**
 * /crm — the native CRM (spec: project-docs/specs/crm_app.md §5).
 *
 * Views over one dataset: Deals (the landing view, as a board or a list), Leads
 * (a list or a board of Lead Status columns), and a list for Contacts and
 * Companies on the shared list contract. CRM-Q3 removed the "Pipeline" row: the
 * board is a layout of Deals, and a picker chooses the pipeline when the org
 * has two or more. `?tab=pipeline` and `?tab=board` still open Deals as a board. Since WS-53 CRM-U1 the app opens
 * with `AppTopBar`, and a rail of `RailRow`s (`components/CrmRail.tsx`)
 * replaced the tab strip. The rail writes the same `?tab=` the tabs did, so
 * every old link still opens its view. The record sheet opens OVER
 * whichever list is showing, driven by `?deal=`/`?lead=`/`?contact=`/
 * `?organization=` — v1 has no saved-views table, so view state lives in the
 * URL and canned views are code.
 *
 * ⚠️ Everything server-shaped lives elsewhere on purpose: the request
 * contract in lib/filters.ts, the board geometry in lib/board.ts, the URL
 * grammar in lib/urlState.ts, the conversion rules in lib/convert.ts — each
 * pure and unit-tested. This file is composition and effects.
 */

import Icon from "@/components/Icon";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AppTopBar } from "@/components/AppTopBar";
import { useMobileDrawer } from "@/components/AppShell";
import FilterPills from "@/components/FilterPills";
import { ModeSwitch } from "@/components/ModeSwitch";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import SelectButton from "@/components/ui/SelectButton";
import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { useAccess } from "@/components/AccessProvider";
import { ShellJob } from "@/lib/shell/doJob";
import { focusPageFilter } from "@/lib/shell/pageFilter";
import { NEW_DEAL_MIRROR, NEW_LEAD_MIRROR, refusalOf } from "./lib/access";
import { CrmModeContext } from "./lib/modeContext";
import {
  CRM_JOB_IDS,
  afterDialogsClose,
  findRecordPlan,
  focusAskFor,
  logCallPlan,
  type FocusAsk,
  type JobPlan,
} from "./lib/shellJobs";
import { hasCapability } from "@/lib/access";
import { railClass, useRailFold } from "@/lib/railFold";
import { AccessSection } from "./components/AccessPanel";
import SetupHubSection from "./components/SetupHub";
import CrmBoundary from "./components/CrmBoundary";
import ConvertModal from "./components/ConvertModal";
import CrmRail, { type RailSettings, type RailViewActions } from "./components/CrmRail";
import FilterBar from "./components/FilterBar";
import FirstOpen from "./components/FirstOpen";
import { ImportProgress, SetupLink, StalledBanner } from "./components/MirrorBanner";
import KanbanBoard from "./components/KanbanBoard";
import MoveModal from "./components/MoveModal";
import PipelineSettings from "./components/PipelineSettings";
import QuickCreateModal from "./components/QuickCreateModal";
import RecordList from "./components/RecordList";
import Reports from "./components/Reports";
import RecordSheet from "./components/RecordSheet";
import ModuleList from "./components/ModuleList";
import ModuleRecordSheet from "./components/ModuleRecordSheet";
import { moduleLabel, type ModuleLink } from "./lib/modules";
import { openModuleRecord, selectModule } from "./lib/urlState";
import { useModules, useSourceLabels } from "./lib/useCrmMode";
import * as api from "./lib/api";
import { CrmError } from "./lib/api";
import {
  ADMIN_CAPABILITY,
  canCreate,
  returnTarget,
  mirrorStalled,
  modeLine,
  returnNotice,
  startFailureMessage,
  syncFailureMessage,
  type ConnectionInfo,
  type CrmMode,
  type SyncStatus,
} from "./lib/connect";
import {
  boardTotals,
  orderLanes,
  missingRequiredFields,
  needsLostReason,
  type DealMove,
  type RequireableField,
} from "./lib/board";
import { readShowEmpty, showEmptyStages, visibleLanes, writeShowEmpty } from "./lib/emptyStages";
import { readCardSort, writeCardSort, type CardSort } from "./lib/nextActivity";
import { chatScope } from "@/lib/sessions";
import { activeChip, applyChip, chipsFor } from "./lib/filters";
import { readColumns } from "./lib/fields";
import { pruneFilters } from "./lib/filterBar";
import {
  activeSavedView,
  applyView,
  canEditView,
  captureView,
  deleteCopy,
  resolveBareLink,
  viewDiverges,
  type SavedView,
} from "./lib/savedViews";
import { invalidateViews, useAllSavedViews } from "./lib/useSavedViews";
import {
  orderedPipelines,
  readRemembered,
  remember,
  resolvePipeline,
  showPicker,
} from "./lib/pipelines";
import { compactMoney } from "./lib/format";
import { setRefusalSink, useCrmStore } from "./lib/store";
import type { EntitySlug, Lead, Status } from "./lib/types";
import {
  applySort,
  closeRecord,
  hasBoard,
  isBoardView,
  isEntity,
  openRecord,
  parseView,
  selectLayout,
  selectPipeline,
  selectRecordTab,
  selectSection,
  selectTab,
  viewHref,
  type CrmLayout,
  type CrmView,
  type SheetParam,
} from "./lib/urlState";
import {
  invalidateCrmMode,
  useCrmMode,
  useFieldDefs,
  useFieldLayout,
  useOwners,
  usePipelines,
  useSyncStatus,
} from "./lib/useCrmMode";
import { boundaryKey } from "./lib/boundary";
import { pushView, replaceView } from "./lib/history";
import {
  sectionLabel,
  sectionRedirect,
  shownSection,
  visibleSections,
  withShownSection,
  type SettingsSectionId,
} from "./lib/settingsSections";
import { convertSkippedNote } from "./lib/convertMapping";
import { viewLabel } from "./lib/views";

/** Which URL parameter opens a record of each collection. */
const PARAM_FOR: Record<EntitySlug, SheetParam> = {
  deals: "deal",
  leads: "lead",
  contacts: "contact",
  organizations: "organization",
};

/** What the page needs to know about the mode and the mirror (CRM-Z7). */
type MirrorContext = {
  mode: CrmMode;
  /** `null` when the mirror is off. */
  connection: ConnectionInfo | null;
  sync: SyncStatus | null;
  isAdmin: boolean;
  /** The mode read failed, so `mode` may be a guess (CRM-C1 review). */
  modeReadFailed: boolean;
  connecting: boolean;
  syncing: boolean;
  onConnect: () => void;
  onSyncNow: () => void;
  onDisconnect: () => Promise<void>;
};

function CrmPageInner({ ctx }: { ctx: MirrorContext }) {
  const toast = useToast();
  const searchParams = useSearchParams();
  const view = useMemo(
    () => parseView(searchParams.toString()),
    [searchParams]
  );

  const store = useCrmStore();
  // The mirrored generic Zoho modules, for the rail (CRM-Q6b). Native: none.
  const { modules } = useModules(ctx.mode === "mirror");
  // CRM-Q17a-2 item 7: in Mirror mode, the source's names of the core
  // entities. A Native CRM, or an org off the list, keeps the CRM's names.
  const labels = useSourceLabels(ctx.mode === "mirror");
  /** The Refresh button of a module list reads page 1 again. */
  const [moduleReload, setModuleReload] = useState(0);
  const { isMobile } = useViewMode();
  // The deal pipelines (CRM-Q3). Null until read, and null when the gateway
  // has no pipelines route: then there is one implicit pipeline and no picker.
  const { pipelines, loading: pipelinesLoading } = usePipelines(true);
  const [remembered, setRemembered] = useState<string | null>(() => readRemembered());
  /** The pipeline a request filters by, or null for "no filter". */
  const pipelineId = resolvePipeline(pipelines, view.pipelineId, remembered);
  /** The view the loaders read: the URL's view plus the pipeline in effect. */
  /** The collection whose records the filters narrow: a list or a board. */
  const filterEntity: EntitySlug | null = isEntity(view.tab) ? view.tab : null;
  const { defs: filterDefs, loading: defsLoading } = useFieldDefs(filterEntity);
  /** The owner filter's choices: every member and record owner (CRM-X1). */
  const owners = useOwners(filterEntity !== null);
  // A filter the gateway would refuse is dropped before the fetch (CRM-Q8):
  // a saved view can name a custom field that is gone, and one 422 would
  // empty the whole list.
  const pruned = useMemo(
    () => pruneFilters(view, filterDefs, ctx.mode === "mirror"),
    [view, filterDefs, ctx.mode],
  );
  // The member, for the choices this browser keeps (`lib/emptyStages.ts`,
  // `lib/nextActivity.ts`). Each is kept per member and organization.
  const { access } = useAccess();
  const memberScope = chatScope(access.email, access.organization?.id);
  // The order of the cards in a deal lane (CRM-X3): stage age or next activity.
  const savedSort = useMemo(() => readCardSort(memberScope), [memberScope]);
  const [pickedSort, setPickedSort] = useState<{ scope: string | null; sort: CardSort } | null>(
    null
  );
  const cardSort: CardSort =
    pickedSort && pickedSort.scope === memberScope ? pickedSort.sort : savedSort;
  const loadView = useMemo(
    () => ({ ...pruned.view, pipelineId, cardSort }),
    [pruned.view, pipelineId, cardSort],
  );
  /** Custom filters wait for the definitions that check them. */
  const hasCustom = Object.keys(view.custom).length > 0;
  const waitingForDefs = defsLoading && hasCustom;
  /** The definitions failed: the custom filters cannot be checked, so nothing is fetched. */
  const unchecked = !defsLoading && pruned.unchecked;
  const boardOn = isBoardView(view);
  // Stable keys for the filters that are objects: the load effect watches them.
  const rangesKey = JSON.stringify(loadView.ranges);
  const customKey = JSON.stringify(loadView.custom);
  // Saved views (CRM-Q8): nested in the rail, and applied through `?view=`.
  const allViews = useAllSavedViews();
  const savedList = useMemo(() => Object.values(allViews).flat(), [allViews]);
  const activeSaved = activeSavedView(savedList, view.viewId);
  /** The columns the member picked since the saved view in effect was applied. */
  const [draftColumns, setDraftColumns] = useState<{ viewId: string; keys: string[] } | null>(
    null,
  );
  const [savingView, setSavingView] = useState(false);
  const [deletingView, setDeletingView] = useState<SavedView | null>(null);
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  /** The rail of views: open from `lg` up, folded below, the member's call
   *  inside a band. The one rule My Tasks uses (`lib/railFold.ts`). */
  const rail = useRailFold();
  const [creating, setCreating] = useState<EntitySlug | null>(null);
  /** Local, not in the store: an in-flight download is this button's state,
   *  not the collection's, and reusing `saving` would grey out the sheet. */
  const [exporting, setExporting] = useState(false);
  const [converting, setConverting] = useState<Lead | null>(null);
  /**
   * Where the open record came from, for `j` and `k` (CRM-X4 item 5): the
   * list on screen, or one lane of the board. Null for a record opened from
   * a link, from Cmd-K or from a related list, which has no list behind it.
   */
  const [cameFrom, setCameFrom] = useState<
    { param: SheetParam; lane: string | null } | null
  >(null);
  /** A move the gateway would refuse as sent — held until the modal answers. */
  const [pendingMove, setPendingMove] = useState<{
    entity: "deals" | "leads";
    move: DealMove;
    status: Status;
    needsReason: boolean;
    missing: RequireableField[];
  } | null>(null);

  /**
   * The view before a job link (CRM-X2). The command bar puts `/crm?do=<job>`
   * in place of the whole query, so the open record is gone from the address
   * when the job runs. This keeps the last view that named no job.
   */
  const viewBeforeJob = useRef<CrmView | null>(null);
  useEffect(() => {
    if (!searchParams.get("do")) viewBeforeJob.current = view;
  }, [view, searchParams]);
  /** A `log-call` ask for one record: its Timeline composer opens on Call. */
  const [callAsk, setCallAsk] = useState<{ n: number; id: string } | null>(null);
  /** The list whose search box a job asked to focus, once it is on screen. */
  const focusFor = useRef<FocusAsk | null>(null);
  const [focusAsk, setFocusAsk] = useState(0);
  /** The hint of `log-call` with no record, for the Deals list it opened. */
  const [jobHint, setJobHint] = useState<string | null>(null);

  /** Every navigation in this app is a URL rewrite — Back closes the sheet.
   *  ⚠️ `pushView`, never `router.push`: a router push waits on a server
   *  request before the view changes, and a held request left the rail dead
   *  (`lib/history.ts`). */
  const go = useCallback((next: CrmView) => pushView(viewHref(next)), []);

  // A link that carries only `?view=<id>` opens that view (CRM-Q8).
  const search = searchParams.toString();
  useEffect(() => {
    const opened = resolveBareLink(search, savedList);
    if (opened) replaceView(viewHref(opened));
  }, [search, savedList]);

  useEffect(() => {
    store.loadVocabulary();
    // Loaded once: the vocabulary is small and every tab needs it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Deals waits for the pipeline list, so the board loads once and not twice.
    if (view.tab === "deals" && pipelinesLoading) return;
    if (waitingForDefs || unchecked) return;
    if (boardOn) {
      if (view.tab === "leads") store.loadLeadBoard(loadView);
      else store.loadBoard(loadView);
    }
    // ⚠️ `settings` and `reports` are tabs and NOT collections:
    // `loadList("settings", …)` would request `/crm/settings` and empty the
    // screen with a 404. The vocabulary settings edits is loaded once, above;
    // reports has four reads of its own.
    else if (view.tab === "reports") store.loadReports(loadView);
    else if (isEntity(view.tab)) store.loadList(view.tab, loadView);
    // ⚠️ Every field `listQuery` reads has to be in this list, `sort`/`dir`
    // included: a filter the effect does not watch is a control that changes
    // its own appearance and issues no request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    view.tab,
    view.layout,
    pipelineId,
    pipelinesLoading,
    view.q,
    view.statusId,
    view.includeConverted,
    view.owner,
    view.sort,
    view.dir,
    view.source,
    rangesKey,
    customKey,
    waitingForDefs,
    unchecked,
    cardSort,
  ]);

  useEffect(() => {
    if (view.record) store.loadRecord(view.record.entity, view.record.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.record?.entity, view.record?.id]);

  /** The collection a LIST is showing. A board is not a list, so it is null there. */
  const listEntity: EntitySlug | null = isEntity(view.tab) && !boardOn ? view.tab : null;
  const listFieldDefs = filterDefs;

  // A job asked for the search box of a list (CRM-X2). The address may change
  // a render after the ask, so the ask waits here until that list is on screen.
  // It also waits for the command bar to finish closing, or the bar takes the
  // focus back (`afterDialogsClose`).
  useEffect(() => {
    const { focus, keep } = focusAskFor(focusFor.current, listEntity, Date.now());
    focusFor.current = keep;
    if (!focus) return;
    return afterDialogsClose(() => void focusPageFilter());
  }, [focusAsk, listEntity]);

  /**
   * Run the plan of a job (`lib/shellJobs.ts`). The address changes through
   * `replaceView`, as `shell/doJob.tsx` does, so nothing waits on the
   * server. A replace, not a push: the job link was the last entry, so Back
   * returns to the view before it.
   */
  function runJob(plan: JobPlan): void {
    replaceView(viewHref(plan.view));
    const opened = plan.view.record;
    if (plan.composeCall && opened) {
      setCallAsk((ask) => ({ n: (ask?.n ?? 0) + 1, id: opened.id }));
    }
    if (plan.focusSearch && isEntity(plan.view.tab)) {
      focusFor.current = { entity: plan.view.tab, at: Date.now() };
      setFocusAsk((n) => n + 1);
    }
    setJobHint(plan.hint);
  }
  /** The hint shows on the Deals list it was given for, and nowhere else. */
  const hintShown = jobHint && listEntity === "deals" && !view.record ? jobHint : null;

  /** What the Refresh button re-reads — one branch per kind of tab. */
  function reload(): void {
    if (boardOn) {
      if (view.tab === "leads") void store.loadLeadBoard(loadView);
      else void store.loadBoard(loadView);
    }
    // The settings tab's collection IS the vocabulary the whole app renders
    // from, so its re-read is the same call the page makes on mount.
    else if (view.tab === "settings") void store.loadVocabulary();
    else if (view.tab === "reports") void store.loadReports(loadView);
    else if (view.tab === "module") setModuleReload((n) => n + 1);
    else if (listEntity) void store.loadList(listEntity, loadView);
  }
  const statusesFor = (entity: EntitySlug | null): Status[] =>
    entity === "deals"
      ? store.dealStatuses
      : entity === "leads"
        ? store.leadStatuses
        : [];

  const barDefs = filterDefs;

  /**
   * The custom columns in effect: the view's own, or this browser's. `null`
   * is "nothing saved", and the list then shows the columns of the fill rank.
   * `[]` is "saved empty" (CRM-Q17a-2 item 2). A new view keeps the choice,
   * not the defaults on screen, so it goes on following the fill rank.
   */
  const draftOfView = draftColumns?.viewId === view.viewId ? draftColumns?.keys : undefined;
  const viewColumns: string[] | null = draftOfView ?? activeSaved?.columns ?? null;
  const currentColumns = (): string[] | null =>
    activeSaved ? viewColumns : filterEntity ? readColumns(filterEntity) : null;
  const dirty = activeSaved
    ? viewDiverges(activeSaved, captureView(view, viewColumns, view.pipelineId))
    : false;

  async function saveView(name: string, shared: boolean) {
    if (!filterEntity) return;
    setSavingView(true);
    try {
      const state = captureView(view, currentColumns(), pipelineId);
      const made = await api.createView({ entity: filterEntity, name, shared, ...state });
      invalidateViews();
      setDraftColumns(null);
      go(applyView(made));
      toast.show({ key: "crm-view", variant: "success", title: `Saved ${made.name}` });
    } catch (err) {
      store.setError(err instanceof CrmError ? err.message : "The view was not saved.");
    } finally {
      setSavingView(false);
    }
  }

  async function updateView() {
    if (!activeSaved || !filterEntity) return;
    setSavingView(true);
    try {
      await api.patchView(activeSaved.id, captureView(view, viewColumns, pipelineId));
      invalidateViews();
      setDraftColumns(null);
    } catch (err) {
      store.setError(err instanceof CrmError ? err.message : "The view was not updated.");
    } finally {
      setSavingView(false);
    }
  }

  async function changeView(saved: SavedView, body: Parameters<typeof api.patchView>[1]) {
    try {
      await api.patchView(saved.id, body);
      invalidateViews();
    } catch (err) {
      store.setError(err instanceof CrmError ? err.message : "The view was not changed.");
    }
  }

  async function removeView(saved: SavedView) {
    setSavingView(true);
    try {
      await api.deleteView(saved.id);
      invalidateViews();
      // A deleted view in effect leaves its filters on screen but no longer names it.
      if (view.viewId === saved.id) go({ ...view, viewId: null });
    } catch (err) {
      store.setError(err instanceof CrmError ? err.message : "The view was not deleted.");
    } finally {
      setSavingView(false);
      setDeletingView(null);
    }
  }

  /** What the rail needs to nest and run the views. */
  const railSaved: RailViewActions = {
    views: allViews,
    activeViewId: view.viewId,
    isAdmin: ctx.isAdmin,
    onSelectView: (saved) => {
      setDraftColumns(null);
      go(applyView(saved));
      if (isMobile) closeDrawer();
    },
    onRenameView: (saved, name) => void changeView(saved, { name }),
    onShareView: (saved) => void changeView(saved, { shared: !saved.shared }),
    onDeleteView: setDeletingView,
  };

  /** The rail rows of the generic modules (CRM-Q6b), and where a lookup opens. */
  const railModules = {
    modules,
    active: view.tab === "module" ? (view.module ?? null) : null,
    onSelect: (module: string) => {
      go(selectModule(view, module));
      if (isMobile) closeDrawer();
    },
  };
  function openLink(link: ModuleLink): void {
    if (link.kind === "record") openAlone(openRecord(view, PARAM_FOR[link.entity], link.id));
    else openAlone(openModuleRecord(view, link.module, link.id));
  }

  const chips = listEntity
    ? chipsFor(listEntity, statusesFor(listEntity), view)
    : [];

  const totals = boardTotals(store.lanes);
  const leadCount = store.leadLanes.reduce((n, lane) => n + lane.count, 0);

  // Empty stages on the Deals board (lib/emptyStages.ts). The member's choice
  // is kept in this browser per member and organization. With no choice, the
  // board hides them in Mirror mode and shows them in Native mode.
  const savedEmpty = useMemo(() => readShowEmpty(memberScope), [memberScope]);
  const [pickedEmpty, setPickedEmpty] = useState<{ scope: string | null; show: boolean } | null>(
    null
  );
  const showEmpty = showEmptyStages(
    ctx.mode,
    pickedEmpty && pickedEmpty.scope === memberScope ? pickedEmpty.show : savedEmpty
  );
  // The one column order, for this mode (lib/board.ts `orderLanes`).
  const dealLanes = visibleLanes(orderLanes(store.lanes, ctx.mode), showEmpty);
  const dealBoard = boardOn && view.tab === "deals";
  const mayCreate = canCreate(ctx.mode);

  /** A board drop, or the status pill in the sheet. Same request either way. */
  async function moveDeal(move: DealMove, target: Status) {
    await moveCard("deals", move, target);
  }

  /** The same move for a lead on the Leads board. `move.dealId` holds the lead id. */
  async function moveCard(entity: "deals" | "leads", move: DealMove, target: Status) {
    const onBoard: Array<Record<string, unknown>> =
      entity === "deals"
        ? store.lanes.flatMap((l) => l.rows)
        : store.leadLanes.flatMap((l) => l.rows);
    const deal =
      onBoard.find((d) => d.id === move.dealId) ??
      (store.record as Record<string, unknown> | null);
    // Both gates read forward, in the gateway's own order. It refuses either
    // one BEFORE writing any of the transition's three effects, so a board
    // that discovered them from an error toast would have already animated the
    // card into the lane it is not allowed to be in.
    const needsReason = Boolean(deal && needsLostReason(deal, target));
    // Entry requirements belong to deal stages. A lead status requires nothing.
    const missing = deal && entity === "deals" ? missingRequiredFields(deal, target) : [];
    if (needsReason || missing.length > 0) {
      // The picker the organization requirement needs. Requested here rather
      // than on mount because the directory is a picker, not a mirror — and
      // unconditionally rather than only for that one field, because the call
      // is idempotent and a branch on which field is missing is a branch that
      // gets it wrong once.
      store.loadDirectories();
      setPendingMove({ entity, move, status: target, needsReason, missing });
      return;
    }
    if (entity === "leads") await store.moveLead(move);
    else await store.moveDeal(move);
    if (view.record) store.loadRecord(view.record.entity, view.record.id);
  }

  /**
   * The ids of the list the open record came from, in the order on screen.
   * Null when no list is behind it, or when the list no longer holds it (a
   * deal moved to another lane), so the arrows hide and the keys do nothing.
   */
  function recordOrder(): string[] | null {
    if (!cameFrom || !view.record || cameFrom.param !== view.record.param) return null;
    const rows: Array<Record<string, unknown>> =
      cameFrom.lane === null
        ? store.rows
        : ((view.tab === "leads" ? store.leadLanes : dealLanes).find(
            (lane) => lane.status.id === cameFrom.lane,
          )?.rows ?? []);
    const ids = rows.map((row) => String(row.id));
    return ids.includes(view.record.id) ? ids : null;
  }

  /** Open a record and forget the list: it came from a link or a related row. */
  function openAlone(next: CrmView): void {
    setCameFrom(null);
    go(next);
  }

  async function convert(body: Record<string, unknown>) {
    if (!converting) return;
    const result = await store.convertLead(converting.id, body);
    const dealId = result?.deal.id ?? null;
    setConverting(null);
    // CRM-C6a: say which lead values the conversion mapping did not copy.
    const note = convertSkippedNote(result?.skipped);
    if (note) {
      toast.show({ key: "crm-convert-skipped", variant: "success", title: "Lead converted", description: note });
    }
    if (dealId) {
      // Land on the deal that was just made: the conversion's whole point is
      // that the work continues there.
      openAlone(openRecord(selectTab(view, "deals"), "deal", dealId));
    }
  }

  // The settings section on screen (CRM-C1, crm_platform.md §20.4). A
  // missing or unknown section goes to the caller's default through
  // `replaceView`, so Back does not return to the bad URL. A known section
  // the caller cannot open draws the default and keeps its URL. Nothing is
  // rewritten while the access read loads or is stale, or while the mode
  // read failed: then the caller is a guess, and an admin's link would be
  // lost (`sectionRedirect`).
  const { loading: accessLoading, stale: accessStale } = useAccess();
  // CRM-C6a: the Native `field-mapping` section needs `studio_admin` of the
  // layout. The read is shared with the Fields card, which reads leads first.
  const studioProbe = useFieldLayout(ctx.isAdmin && ctx.mode !== "mirror" ? "leads" : null);
  const studioAdmin = studioProbe.layout?.studio_admin === true;
  const sectionCtx = useMemo(
    () => ({ isAdmin: ctx.isAdmin, mode: ctx.mode, studioAdmin }),
    [ctx.isAdmin, ctx.mode, studioAdmin],
  );
  const settingsSection = shownSection(view.section, sectionCtx);
  const sectionTrusted = !accessLoading && !accessStale && !ctx.modeReadFailed;
  useEffect(() => {
    const redirect = sectionRedirect(view, sectionCtx, sectionTrusted);
    if (redirect) replaceView(viewHref({ ...view, section: redirect }));
  }, [view, sectionCtx, sectionTrusted]);

  // The scope line beside the name. CRM-Z7 puts the mode here ("Native", or
  // "Synced from Zoho · 4 min ago"). Until a mode exists, it says what the
  // view on screen holds.
  const viewLine =
    boardOn && view.tab === "deals"
      ? // Weighted sits next to the raw total rather than replacing it:
        // "₹4.2Cr in the pipeline" is what is on the table and "₹1.6Cr
        // weighted" is what the forecast believes, and a line that showed
        // only one of them would be answering a different question.
        `${totals.count} open deals · ${compactMoney(totals.amount)} in the pipeline · ${compactMoney(totals.weighted)} weighted`
      : boardOn
        ? `${leadCount} leads by status`
        : view.tab === "settings"
          ? `Settings · ${sectionLabel(settingsSection)}`
          : view.tab === "reports"
            ? "Forecast, funnel, win rate and who is carrying what"
            : view.tab === "module"
              ? moduleLabel(modules, view.module)
              : // A list view names itself. With the rail folded, the four
              // lists share one layout, and this line is all that tells
              // Deals from Contacts.
              viewLabel(view.tab, labels);
  const mode = modeLine({ mode: ctx.mode, connection: ctx.connection, sync: ctx.sync });
  // CRM-X0: the mode part links an admin to the setup hub.
  const subtitle = mode ? (
    <>
      <SetupLink isAdmin={ctx.isAdmin}>{mode}</SetupLink>
      {` · ${viewLine}`}
    </>
  ) : (
    viewLine
  );

  /** A rail row, from the desktop column or the phone's drawer. */
  function chooseView(tab: CrmView["tab"]): void {
    // A click on the view on screen adds no history entry (CRM-C1 review).
    const next = selectTab(view, tab);
    if (next !== view) go(next);
    if (isMobile) closeDrawer();
  }

  /** A settings section, from a child row of the rail (CRM-C1). */
  function chooseSection(section: SettingsSectionId): void {
    const next = selectSection(view, section);
    if (next !== view) go(next);
    if (isMobile) closeDrawer();
  }
  /** The child rows of the settings row: the sections this caller can open. */
  const railSettings: RailSettings = {
    sections: visibleSections(sectionCtx),
    active: view.tab === "settings" ? settingsSection : null,
    onSelect: chooseSection,
  };

  // The app's verb sits next to its name, as Capture does in My Tasks. It is
  // the quick-create entry the old header held, at the bar's size.
  // In Mirror mode a record is made in Zoho, so the verb is gone (spec §3.3).
  const actions =
    mayCreate && view.tab !== "settings" && view.tab !== "reports" && view.tab !== "module" ? (
      <Button
        variant="secondary"
        size="sm"
        icon="Plus"
        onClick={() => setCreating(isEntity(view.tab) ? view.tab : "deals")}
      >
        New
      </Button>
    ) : null;

  const picker =
    view.tab === "deals" && showPicker(pipelines) && pipelines ? (
      <SelectButton
        label="Pipeline"
        value={pipelineId ?? ""}
        options={orderedPipelines(pipelines).map((p) => ({ value: p.id, label: p.name }))}
        defaultValue=""
        onChange={(id) => {
          remember(id);
          setRemembered(id);
          go(selectPipeline(view, id));
        }}
        widthClass="w-40"
      />
    ) : null;

  const tools = (
    <>
      {picker}
      {hasBoard(view.tab) && (
        // The list or board switch (CRM-Q3). The shared control that Projects
        // and My Tasks draw, so the CRM adds no second look for one choice.
        <ModeSwitch<CrmLayout>
          modes={[
            { id: "list", icon: "List" },
            { id: "board", icon: "Kanban" },
          ]}
          mode={view.layout}
          onPick={(next) => go(selectLayout(view, next))}
          label={`${viewLabel(view.tab, labels)} layout`}
        />
      )}
      {dealBoard && (
        // Show or hide the stages that hold no deal under the filters on screen.
        <Button
          variant="ghost"
          size={isMobile ? "icon-sm" : "sm"}
          icon={showEmpty ? "Eye" : "EyeOff"}
          selected={showEmpty}
          aria-label="Show empty stages"
          title={showEmpty ? "Hide the stages that hold no deal" : "Show the stages that hold no deal"}
          onClick={() => {
            const next = !showEmpty;
            writeShowEmpty(memberScope, next);
            setPickedEmpty({ scope: memberScope, show: next });
          }}
        >
          {isMobile ? null : "Empty stages"}
        </Button>
      )}
      {dealBoard && (
        // The order of the cards in each lane (CRM-X3). Off: stage age, as
        // before. On: the soonest next activity first. The gateway sorts,
        // because a lane returns one page of its cards.
        <Button
          variant="ghost"
          size={isMobile ? "icon-sm" : "sm"}
          icon="CalendarClock"
          selected={cardSort === "next_activity"}
          aria-label="Order cards by next activity"
          title={
            cardSort === "next_activity"
              ? "Cards are in order of next activity. Click to order them by time in stage"
              : "Cards are in order of time in stage. Click to order them by next activity"
          }
          onClick={() => {
            const next: CardSort = cardSort === "next_activity" ? "stage_age" : "next_activity";
            writeCardSort(memberScope, next);
            setPickedSort({ scope: memberScope, sort: next });
          }}
        >
          {isMobile ? null : "Next activity"}
        </Button>
      )}
      {listEntity && (
        // WS-26i-export — the filter that is on screen, as a CSV.
        //
        // ⚠️ Fetched rather than navigated to (see lib/api.exportRecords).
        // The endpoint REFUSES a filter wider than its row cap, naming the
        // matched count, and `window.location = …` would turn that refusal
        // into a tab full of JSON instead of a sentence in the banner below.
        //
        // It is on the LISTS only: the board is a page of each lane and
        // reports are aggregates, so neither has a row set an export could
        // honestly claim to be "what you were looking at".
        <Button
          variant="ghost"
          size={isMobile ? "icon-sm" : "sm"}
          icon="Download"
          loading={exporting}
          aria-label="Export CSV"
          title="Export what is on screen as a CSV"
          onClick={async () => {
            setExporting(true);
            await store.exportList(listEntity, loadView);
            setExporting(false);
          }}
        >
          {isMobile ? null : "Export"}
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={reload}
        aria-label="Refresh"
        title="Refresh"
      >
        <Icon name="RefreshCw" size={14} className={store.loading ? "animate-spin" : ""} />
      </Button>
    </>
  );

  return (
    // CRM-C2: the list, the reports and the status pill read the mode here.
    <CrmModeContext.Provider value={ctx.mode}>
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {/* ═══ The app's title bar (AGENTS.md rule 11, CRM-U1) ═══
          The rail toggle, the name and the scope, the New verb, then the
          tools at the right end. On a phone `AppTopBar` draws the compact
          bar by itself. There the title names the view on screen, and a tap
          opens the views in the shell's drawer, as the Email inbox does. */}
      <AppTopBar
        rail={{ open: rail.open, onToggle: rail.toggle, noun: "the views" }}
        title={
          isMobile ? (
            <button
              type="button"
              onClick={() =>
                openDrawer(
                  <nav aria-label="CRM views" className="p-2">
                    <CrmRail
                      tab={view.tab}
                      onSelect={chooseView}
                      saved={railSaved}
                      modules={railModules}
                      settings={railSettings}
                      labels={labels}
                    />
                  </nav>
                )
              }
              className="inline-flex max-w-full items-center gap-1 align-middle tech-transition hover:opacity-80"
            >
              <span className="truncate">
                {view.tab === "module" ? moduleLabel(modules, view.module) : viewLabel(view.tab, labels)}
              </span>
              <Icon name="ChevronDown" size={14} className="shrink-0 text-muted-foreground" />
            </button>
          ) : (
            "CRM"
          )
        }
        subtitle={subtitle}
        actions={actions}
        tools={tools}
      />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* The rail of views. It folds below `lg` and the toggle in the bar
            opens it (`useRailFold`, the My Tasks rule). A phone has no
            column: the views live in the drawer the title opens. */}
        {!isMobile && rail.open ? (
          <nav
            aria-label="CRM views"
            className={`w-56 shrink-0 overflow-y-auto border-r border-border bg-card p-2 ${railClass(rail.settled)}`}
          >
            <CrmRail
              tab={view.tab}
              onSelect={chooseView}
              saved={railSaved}
              modules={railModules}
              settings={railSettings}
              labels={labels}
            />
          </nav>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {ctx.mode === "mirror" && mirrorStalled(ctx.connection) && (
            // CRM-X0: the banner leads to the setup hub, which offers the fix.
            <StalledBanner
              isAdmin={ctx.isAdmin}
              status={ctx.connection?.status ?? null}
              onOpenSetup={() => chooseSection("setup")}
            />
          )}
          {ctx.mode === "mirror" && ctx.sync?.state === "running" && (
            <ImportProgress status={ctx.sync} />
          )}
          {chips.length > 0 && (
            <FilterPills
              items={chips}
              activeId={activeChip(view)}
              onChange={(id) => go(applyChip(view, id))}
            />
          )}

          {filterEntity && (
            <FilterBar
              entity={filterEntity}
              view={view}
              onChange={go}
              owners={owners}
              search={!boardOn}
              fieldDefs={barDefs}
              mirror={ctx.mode === "mirror"}
              removed={waitingForDefs ? [] : pruned.removed}
              unchecked={unchecked}
              activeView={activeSaved}
              dirty={dirty}
              canEdit={activeSaved ? canEditView(activeSaved, ctx.isAdmin) : false}
              isAdmin={ctx.isAdmin}
              saving={savingView}
              onSave={saveView}
              onUpdate={updateView}
              onReset={() => {
                if (!activeSaved) return;
                setDraftColumns(null);
                go(applyView(activeSaved));
              }}
            />
          )}

          {store.error && (
            // A refusal must surface: a control that silently no-ops reads as
            // broken, and a stale row after a 409 reads as success.
            <div className="flex shrink-0 items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-4 py-2">
              <p className="flex-1 text-xs text-destructive">{store.error}</p>
              <Button
                variant="ghost"
                size="icon-xs"
                icon="X"
                onClick={() => store.setError(null)}
                aria-label="Dismiss"
              />
            </div>
          )}

          {hintShown && (
            // `log-call` with no record open (CRM-X2): the next step, in one
            // line. A record opened from this list takes the call.
            <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/40 px-4 py-2">
              <Icon name="PhoneCall" size={14} className="shrink-0 text-muted-foreground" />
              <p className="flex-1 text-xs text-muted-foreground">{hintShown}</p>
              <Button
                variant="ghost"
                size="icon-xs"
                icon="X"
                onClick={() => setJobHint(null)}
                aria-label="Dismiss"
              />
            </div>
          )}

          {/* One boundary around the view. A throw ends on a sentence and a
              Reload button, and the rail stays. The key clears it when the
              member picks another view (CRM-Q3, spec section 18.6). */}
          <CrmBoundary key={boundaryKey(withShownSection(view, sectionCtx), pipelineId)}>
          {boardOn ? (
            <KanbanBoard
              entity={view.tab === "leads" ? "leads" : "deals"}
              lanes={view.tab === "leads" ? store.leadLanes : dealLanes}
              hiddenEmpty={view.tab === "leads" ? 0 : store.lanes.length - dealLanes.length}
              loading={store.loading}
              readOnly={ctx.mode === "mirror"}
              // CRM-C2: the lane colour reads the mode, and the card of the
              // open record wears the cursor ring.
              mode={ctx.mode}
              cursorId={view.record?.id ?? null}
              onMove={
                view.tab === "leads"
                  ? (move, target) => void moveCard("leads", move, target)
                  : moveDeal
              }
              onOpen={(id) => {
                // `j` and `k` walk the cards of this lane (CRM-X4 item 5).
                const lanes = view.tab === "leads" ? store.leadLanes : dealLanes;
                const lane = lanes.find((l) => l.rows.some((row) => row.id === id));
                const param = view.tab === "leads" ? "lead" : "deal";
                setCameFrom({ param, lane: lane?.status.id ?? null });
                go(openRecord(view, param, id));
              }}
              onCreate={mayCreate ? () => setCreating(view.tab === "leads" ? "leads" : "deals") : undefined}
            />
          ) : view.tab === "module" && view.module ? (
            // A generic Zoho module (CRM-Q6b): the shared list, read only.
            <ModuleList
              module={view.module}
              label={moduleLabel(modules, view.module)}
              view={view}
              onChange={go}
              onOpen={(id) => openAlone(openModuleRecord(view, view.module!, id))}
              reloadToken={moduleReload}
            />
          ) : view.tab === "reports" ? (
            <Reports reports={store.reports} loading={store.loading} />
          ) : view.tab === "settings" ? (
            <PipelineSettings
              section={settingsSection}
              setup={
                // CRM-X0: the one setup hub. It holds the connection too.
                ctx.isAdmin ? (
                  <SetupHubSection
                    mode={ctx.mode}
                    connection={ctx.connection}
                    sync={ctx.sync}
                    syncing={ctx.syncing}
                    connecting={ctx.connecting}
                    onSyncNow={ctx.onSyncNow}
                    onConnect={ctx.onConnect}
                    onDisconnect={ctx.onDisconnect}
                  />
                ) : undefined
              }
              access={ctx.isAdmin ? <AccessSection /> : undefined}
              pipelines={pipelines}
              showAdminCards={ctx.isAdmin}
              dealStatuses={store.dealStatuses}
              leadStatuses={store.leadStatuses}
              lostReasons={store.lostReasons}
              saving={store.saving}
              readOnly={ctx.mode === "mirror"}
              studioAdmin={studioAdmin}
              onSaveStatus={store.saveStatus}
              onRemoveStatus={store.removeStatus}
              onReorderStatuses={store.reorderStatuses}
              onSaveLostReason={store.saveLostReason}
              onRemoveLostReason={store.removeLostReason}
              onReorderLostReasons={store.reorderLostReasons}
            />
          ) : (
            // `listEntity` is non-null here by construction — the branches
            // above are the only views that are not a list — but it is read
            // through the guard rather than cast, because the cast is what made
            // `?tab=settings` render a list of nothing on the way in.
            listEntity && (
              <RecordList
                entity={listEntity}
                fieldDefs={listFieldDefs}
                columnKeys={activeSaved ? viewColumns : undefined}
                onColumnsChange={(keys) =>
                  view.viewId && setDraftColumns({ viewId: view.viewId, keys })
                }
                rows={store.rows}
                total={store.total}
                loading={store.loading}
                statuses={statusesFor(listEntity)}
                onLoadMore={() => void store.loadMore()}
                loadingMore={store.loadingMore}
                hasMore={store.hasMore}
                sort={view.sort}
                direction={view.dir}
                onSort={(key) => go(applySort(view, key))}
                onOpen={(id) => {
                  // `j` and `k` walk the rows of this list (CRM-X4 item 5).
                  setCameFrom({ param: PARAM_FOR[listEntity], lane: null });
                  go(openRecord(view, PARAM_FOR[listEntity], id));
                }}
              />
            )
          )}
          </CrmBoundary>
        </main>
      </div>

      {deletingView && (
        <ConfirmDialog
          open
          title={deleteCopy(deletingView).title}
          body={deleteCopy(deletingView).body}
          confirmLabel="Delete view"
          busy={savingView}
          onCancel={() => setDeletingView(null)}
          onConfirm={() => void removeView(deletingView)}
        />
      )}

      {view.record && (
        <RecordSheet
          entity={view.record.entity}
          labels={labels}
          record={store.record}
          statuses={statusesFor(view.record.entity)}
          timeline={store.timeline}
          dealContacts={store.dealContacts}
          saving={store.saving}
          onClose={() => {
            // The convert and move dialogs open OVER the sheet, and each one
            // is its own `Modal`. Both hear one Escape, so the sheet keeps
            // still while a dialog above it is up: that key, or a click on
            // that dialog, closes the dialog and not the record.
            if (converting || pendingMove) return;
            setCameFrom(null);
            setCallAsk(null);
            go(closeRecord(view));
          }}
          tab={view.recordTab ?? "overview"}
          // A tab replaces the URL, so Back still closes the sheet.
          onTab={(tab) =>
            replaceView(viewHref(selectRecordTab(view, tab)))
          }
          // No step while a dialog is open over the sheet.
          order={converting || pendingMove ? null : recordOrder()}
          // The step replaces the URL too: Back closes the sheet, and does not
          // walk back through every record the keys passed.
          onStep={(id) =>
            replaceView(viewHref(openRecord(view, view.record!.param, id)))
          }
          onPatch={(body) =>
            store.patchRecord(view.record!.entity, view.record!.id, body)
          }
          onMoveStatus={(statusId) => {
            const target = statusesFor(view.record!.entity).find(
              (s) => s.id === statusId
            );
            if (!target) return;
            if (view.record!.entity === "deals") {
              moveDeal(
                {
                  dealId: view.record!.id,
                  fromStatusId: (store.record?.status_id as string) ?? null,
                  toStatusId: statusId,
                },
                target
              );
            } else {
              store.patchRecord(view.record!.entity, view.record!.id, {
                status_id: statusId,
              });
            }
          }}
          onLog={(body) =>
            store.logActivity(view.record!.entity, view.record!.id, body)
          }
          onToggleTask={(activityId, completed) =>
            store.toggleTask(
              activityId,
              completed,
              view.record!.entity,
              view.record!.id
            )
          }
          onConvert={() => {
            store.loadDirectories();
            setConverting(store.record as unknown as Lead);
          }}
          onSetPrimary={(contactId) =>
            store.setPrimaryContact(view.record!.id, contactId)
          }
          onDetach={(contactId) =>
            store.detachContact(view.record!.id, contactId)
          }
          onOpenContact={(contactId) => openAlone(openRecord(view, "contact", contactId))}
          related={store.related}
          // `log-call` asked for THIS record. Another record opens on Note.
          callRequest={callAsk && callAsk.id === view.record.id ? callAsk.n : 0}
          onRetryRelated={() => void store.loadRelated(view.record!.entity, view.record!.id)}
          onOpenRelated={(entity, id) => openAlone(openRecord(view, PARAM_FOR[entity], id))}
        />
      )}

      {view.moduleRecord && (
        // A generic record (CRM-Q6b), on the same record page shell.
        <ModuleRecordSheet
          module={view.moduleRecord.module}
          id={view.moduleRecord.id}
          tab={view.recordTab ?? "overview"}
          // A tab replaces the URL, so Back still closes the sheet (#914 idiom).
          onTab={(tab) => replaceView(viewHref(selectRecordTab(view, tab)))}
          onClose={() => go(closeRecord(view))}
          onOpenLink={openLink}
        />
      )}

      <ShellJob
        id="new-lead"
        onOpen={() => {
          // Mirror mode: Zoho owns the leads, so the form cannot open.
          if (mayCreate) setCreating("leads");
          else toast.show({ key: "crm-new-lead", variant: "error", title: NEW_LEAD_MIRROR });
        }}
      />
      {/* The other three CRM jobs (CRM-X2, spec section 19.6). Each opens a
          form or a list, and none saves. */}
      <ShellJob
        id="new-deal"
        onOpen={() => {
          // Mirror mode: Zoho owns the deals, so the form cannot open.
          if (mayCreate) setCreating("deals");
          else toast.show({ key: "crm-new-deal", variant: "error", title: NEW_DEAL_MIRROR });
        }}
      />
      {/* A call is Metorite-only data, so `log-call` works in Mirror mode too. */}
      <ShellJob id="log-call" onOpen={() => runJob(logCallPlan(viewBeforeJob.current))} />
      <ShellJob id="find-record" onOpen={() => runJob(findRecordPlan(viewBeforeJob.current))} />

      {creating && (
        <QuickCreateModal
          entity={creating}
          saving={store.saving}
          refusal={store.refusal}
          onClose={() => setCreating(null)}
          onCreate={async (body) => {
            const entity = creating;
            const id = await store.createRecord(entity, body);
            // The 422 rule (CRM-C4b): a refused save keeps the form open, with
            // the typed values and the message beside its field.
            if (!id) return;
            setCreating(null);
            openAlone(openRecord(view, PARAM_FOR[entity], id));
          }}
        />
      )}

      {converting && (
        <ConvertModal
          lead={converting}
          contacts={store.contacts}
          organizations={store.organizations}
          saving={store.saving}
          onClose={() => setConverting(null)}
          onConvert={convert}
        />
      )}

      {pendingMove && (
        <MoveModal
          statusName={pendingMove.status.name}
          needsReason={pendingMove.needsReason}
          missing={pendingMove.missing}
          reasons={store.lostReasons}
          organizations={store.organizations}
          saving={store.saving}
          onCancel={() => setPendingMove(null)}
          onConfirm={async (payload) => {
            const { move } = pendingMove;
            setPendingMove(null);
            // The reason and the required fields travel WITH the move — one
            // PATCH that either lands or does not, rather than two that can
            // half-apply, where the half that lands is the one that moved the
            // deal.
            if (pendingMove.entity === "leads") await store.moveLead(move, payload);
            else await store.moveDeal(move, payload);
            if (view.record) store.loadRecord(view.record.entity, view.record.id);
          }}
        />
      )}
    </div>
    </CrmModeContext.Provider>
  );
}

/**
 * The gate (WS-53 CRM-U5, CRM-Z7). It reads the mode and the connection,
 * shows the first-open cards when no mode exists, handles the return from
 * Zoho, and hands the pipeline its context. The pipeline loads nothing until
 * the gate says "ready".
 */
function CrmGate(): ReactNode {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const toast = useToast();
  const { access, loading: accessLoading } = useAccess();
  const isAdmin = hasCapability(access, ADMIN_CAPABILITY);

  const { phase, mode, connection, readFailed } = useCrmMode(isAdmin, accessLoading);
  const [watchToken, setWatchToken] = useState(0);
  const sync = useSyncStatus(connection !== null && mode === "mirror", watchToken);
  const [busy, setBusy] = useState<"native" | "zoho" | null>(null);
  const [syncing, setSyncing] = useState(false);

  // A mirror-guard refusal (409 mirrored_record) is a toast, not a banner.
  useEffect(() => {
    setRefusalSink((text) =>
      toast.show({ key: "crm-mirror-refusal", variant: "error", title: text }),
    );
    return () => setRefusalSink(null);
  }, [toast]);

  // The return from Zoho: say how it went, clear the parameter, re-read.
  const search = searchParams.toString();
  const handled = useRef("");
  useEffect(() => {
    const notice = returnNotice(search);
    if (!notice) {
      handled.current = "";
      return;
    }
    if (handled.current === search) return;
    handled.current = search;
    if (notice.kind === "connected") {
      toast.show({ key: "crm-connect-return", variant: "success", title: "Zoho CRM connected" });
      // The effect follows the URL, an external system, and starts a watch.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWatchToken((n) => n + 1);
    } else {
      toast.show({ key: "crm-connect-return", variant: "error", title: notice.message });
    }
    invalidateCrmMode();
    // CRM-X0: another Zoho account opens the setup hub (`returnTarget`).
    const rest = returnTarget(search);
    replaceView(rest ? `${pathname}?${rest}` : pathname);
  }, [search, pathname, toast]);

  async function connectZoho() {
    setBusy("zoho");
    try {
      const { url } = await api.startZohoConnect();
      window.location.assign(url);
    } catch (err) {
      const e = err instanceof CrmError ? err : null;
      toast.show({
        key: "crm-connect-return",
        variant: "error",
        title: startFailureMessage(e?.status ?? 0, e?.code),
      });
      setBusy(null);
    }
  }

  async function chooseNative() {
    setBusy("native");
    try {
      await api.chooseNativeMode();
    } catch (err) {
      // A 409 means the mode changed meanwhile. The re-read shows the truth.
      toast.show({
        key: "crm-mode",
        variant: "error",
        title: err instanceof CrmError ? err.message : "The CRM did not start. Try again.",
      });
    } finally {
      setBusy(null);
      invalidateCrmMode();
    }
  }

  async function syncNow() {
    setSyncing(true);
    try {
      await api.syncZohoNow();
      toast.show({ key: "crm-sync", variant: "success", title: "Sync started" });
      setWatchToken((n) => n + 1);
    } catch (err) {
      const e = err instanceof CrmError ? err : null;
      toast.show({
        key: "crm-sync",
        variant: "error",
        title: syncFailureMessage(e?.status ?? 0, e?.code),
      });
    } finally {
      setSyncing(false);
      invalidateCrmMode();
    }
  }

  async function disconnect() {
    try {
      await api.disconnectCrm();
      toast.show({ key: "crm-sync", variant: "success", title: "Zoho disconnected" });
    } catch (err) {
      toast.show({
        key: "crm-sync",
        variant: "error",
        title: err instanceof CrmError ? err.message : "Zoho did not disconnect. Try again.",
      });
    } finally {
      invalidateCrmMode();
    }
  }

  if (phase !== "ready") {
    // The CRM jobs land here too, and they must say why nothing opens.
    const refusal = refusalOf(phase);
    return (
      <>
        {refusal &&
          // Every CRM job lands here too, and each must say why nothing opens.
          CRM_JOB_IDS.map((id) => (
            <ShellJob
              key={id}
              id={id}
              onOpen={() => toast.show({ key: `crm-${id}`, variant: "error", title: refusal })}
            />
          ))}
        <FirstOpen
          phase={phase}
          busy={busy}
          onNative={chooseNative}
          onConnectZoho={connectZoho}
        />
      </>
    );
  }

  return (
    <CrmPageInner
      ctx={{
        mode,
        connection,
        sync,
        isAdmin,
        modeReadFailed: readFailed,
        connecting: busy === "zoho",
        syncing,
        onConnect: connectZoho,
        onSyncNow: syncNow,
        onDisconnect: disconnect,
      }}
    />
  );
}

export default function CrmPage() {
  return (
    <Suspense fallback={null}>
      <CrmGate />
    </Suspense>
  );
}
