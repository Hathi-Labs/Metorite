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
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
import { NEW_LEAD_MIRROR, refusalOf } from "./lib/access";
import { hasCapability } from "@/lib/access";
import { railClass, useRailFold } from "@/lib/railFold";
import { AccessSection } from "./components/AccessPanel";
import { SetupSection } from "./components/SetupPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import CrmBoundary from "./components/CrmBoundary";
import ConvertModal from "./components/ConvertModal";
import CrmRail, { type RailViewActions } from "./components/CrmRail";
import FilterBar from "./components/FilterBar";
import FirstOpen from "./components/FirstOpen";
import { ImportProgress, ReconnectBanner } from "./components/MirrorBanner";
import KanbanBoard from "./components/KanbanBoard";
import MoveModal from "./components/MoveModal";
import PipelineSettings from "./components/PipelineSettings";
import QuickCreateModal from "./components/QuickCreateModal";
import RecordList from "./components/RecordList";
import Reports from "./components/Reports";
import RecordSheet from "./components/RecordSheet";
import * as api from "./lib/api";
import { CrmError } from "./lib/api";
import {
  ADMIN_CAPABILITY,
  canCreate,
  clearReturnParams,
  modeLine,
  needsReconnect,
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
  selectTab,
  viewHref,
  type CrmLayout,
  type CrmView,
  type SheetParam,
} from "./lib/urlState";
import { invalidateCrmMode, useCrmMode, useFieldDefs, usePipelines, useSyncStatus } from "./lib/useCrmMode";
import { boundaryKey } from "./lib/boundary";
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
  connecting: boolean;
  syncing: boolean;
  onConnect: () => void;
  onSyncNow: () => void;
  onDisconnect: () => Promise<void>;
};

function CrmPageInner({ ctx }: { ctx: MirrorContext }) {
  const router = useRouter();
  const toast = useToast();
  const searchParams = useSearchParams();
  const view = useMemo(
    () => parseView(searchParams.toString()),
    [searchParams]
  );

  const store = useCrmStore();
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
  // A filter the gateway would refuse is dropped before the fetch (CRM-Q8):
  // a saved view can name a custom field that is gone, and one 422 would
  // empty the whole list.
  const pruned = useMemo(
    () => pruneFilters(view, filterDefs, ctx.mode === "mirror"),
    [view, filterDefs, ctx.mode],
  );
  const loadView = useMemo(
    () => ({ ...pruned.view, pipelineId }),
    [pruned.view, pipelineId],
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
  /** A move the gateway would refuse as sent — held until the modal answers. */
  const [pendingMove, setPendingMove] = useState<{
    entity: "deals" | "leads";
    move: DealMove;
    status: Status;
    needsReason: boolean;
    missing: RequireableField[];
  } | null>(null);

  /** Every navigation in this app is a URL rewrite — Back closes the sheet. */
  const go = useCallback(
    (next: CrmView) => router.push(viewHref(next), { scroll: false }),
    [router]
  );

  // A link that carries only `?view=<id>` opens that view (CRM-Q8).
  const search = searchParams.toString();
  useEffect(() => {
    const opened = resolveBareLink(search, savedList);
    if (opened) router.replace(viewHref(opened), { scroll: false });
  }, [search, savedList, router]);

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
  ]);

  useEffect(() => {
    if (view.record) store.loadRecord(view.record.entity, view.record.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.record?.entity, view.record?.id]);

  /** The collection a LIST is showing. A board is not a list, so it is null there. */
  const listEntity: EntitySlug | null = isEntity(view.tab) && !boardOn ? view.tab : null;
  const listFieldDefs = filterDefs;

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
    else if (listEntity) void store.loadList(listEntity, loadView);
  }
  const statusesFor = (entity: EntitySlug | null): Status[] =>
    entity === "deals"
      ? store.dealStatuses
      : entity === "leads"
        ? store.leadStatuses
        : [];

  const barDefs = filterDefs;
  const barRows: Array<Record<string, unknown>> = boardOn
    ? (view.tab === "leads" ? store.leadLanes : store.lanes).flatMap(
        (lane) => lane.rows as Array<Record<string, unknown>>,
      )
    : store.rows;

  /** The custom columns in effect: the view's own, or this browser's. */
  const draftOfView = draftColumns?.viewId === view.viewId ? draftColumns?.keys : undefined;
  const viewColumns = draftOfView ?? activeSaved?.columns ?? [];
  const currentColumns = (): string[] =>
    activeSaved ? viewColumns : filterEntity ? readColumns(filterEntity) : [];
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

  const chips = listEntity
    ? chipsFor(listEntity, statusesFor(listEntity), view)
    : [];

  const totals = boardTotals(store.lanes);
  const leadCount = store.leadLanes.reduce((n, lane) => n + lane.count, 0);

  // Empty stages on the Deals board (lib/emptyStages.ts). The member's choice
  // is kept in this browser per member and organization. With no choice, the
  // board hides them in Mirror mode and shows them in Native mode.
  const { access } = useAccess();
  const memberScope = chatScope(access.email, access.organization?.id);
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

  async function convert(body: Record<string, unknown>) {
    if (!converting) return;
    const dealId = await store.convertLead(converting.id, body);
    setConverting(null);
    if (dealId) {
      // Land on the deal that was just made: the conversion's whole point is
      // that the work continues there.
      go(openRecord(selectTab(view, "deals"), "deal", dealId));
    }
  }

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
          ? "Setup, access, pipelines and statuses"
          : view.tab === "reports"
            ? "Forecast, funnel, win rate and who is carrying what"
            : // A list view names itself. With the rail folded, the four
              // lists share one layout, and this line is all that tells
              // Deals from Contacts.
              viewLabel(view.tab);
  const mode = modeLine({ mode: ctx.mode, connection: ctx.connection, sync: ctx.sync });
  const subtitle = mode ? `${mode} · ${viewLine}` : viewLine;

  /** A rail row, from the desktop column or the phone's drawer. */
  function chooseView(tab: CrmView["tab"]): void {
    go(selectTab(view, tab));
    if (isMobile) closeDrawer();
  }

  // The app's verb sits next to its name, as Capture does in My Tasks. It is
  // the quick-create entry the old header held, at the bar's size.
  // In Mirror mode a record is made in Zoho, so the verb is gone (spec §3.3).
  const actions =
    mayCreate && view.tab !== "settings" && view.tab !== "reports" ? (
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
          label={`${viewLabel(view.tab)} layout`}
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
                    <CrmRail tab={view.tab} onSelect={chooseView} saved={railSaved} />
                  </nav>
                )
              }
              className="inline-flex max-w-full items-center gap-1 align-middle tech-transition hover:opacity-80"
            >
              <span className="truncate">{viewLabel(view.tab)}</span>
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
            <CrmRail tab={view.tab} onSelect={chooseView} saved={railSaved} />
          </nav>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {ctx.mode === "mirror" && needsReconnect(ctx.connection) && (
            <ReconnectBanner
              isAdmin={ctx.isAdmin}
              busy={ctx.connecting}
              onReconnect={ctx.onConnect}
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
              rows={barRows}
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

          {/* One boundary around the view. A throw ends on a sentence and a
              Reload button, and the rail stays. The key clears it when the
              member picks another view (CRM-Q3, spec section 18.6). */}
          <CrmBoundary key={boundaryKey(view, pipelineId)}>
          {boardOn ? (
            <KanbanBoard
              entity={view.tab === "leads" ? "leads" : "deals"}
              lanes={view.tab === "leads" ? store.leadLanes : dealLanes}
              hiddenEmpty={view.tab === "leads" ? 0 : store.lanes.length - dealLanes.length}
              loading={store.loading}
              readOnly={ctx.mode === "mirror"}
              onMove={
                view.tab === "leads"
                  ? (move, target) => void moveCard("leads", move, target)
                  : moveDeal
              }
              onOpen={(id) =>
                go(openRecord(view, view.tab === "leads" ? "lead" : "deal", id))
              }
              onCreate={mayCreate ? () => setCreating(view.tab === "leads" ? "leads" : "deals") : undefined}
            />
          ) : view.tab === "reports" ? (
            <Reports reports={store.reports} loading={store.loading} />
          ) : view.tab === "settings" ? (
            <PipelineSettings
              setup={
                ctx.isAdmin ? <SetupSection mode={ctx.mode} connection={ctx.connection} /> : undefined
              }
              access={ctx.isAdmin ? <AccessSection /> : undefined}
              connection={
                ctx.isAdmin && ctx.connection ? (
                  <ConnectionPanel
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
              pipelines={pipelines}
              showAdminCards={ctx.isAdmin}
              dealStatuses={store.dealStatuses}
              leadStatuses={store.leadStatuses}
              lostReasons={store.lostReasons}
              saving={store.saving}
              readOnly={ctx.mode === "mirror"}
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
                sort={view.sort}
                direction={view.dir}
                onSort={(key) => go(applySort(view, key))}
                onOpen={(id) => go(openRecord(view, PARAM_FOR[listEntity], id))}
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
            go(closeRecord(view));
          }}
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
          onOpenContact={(contactId) => go(openRecord(view, "contact", contactId))}
          related={store.related}
          onRetryRelated={() => void store.loadRelated(view.record!.entity, view.record!.id)}
          onOpenRelated={(entity, id) => go(openRecord(view, PARAM_FOR[entity], id))}
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

      {creating && (
        <QuickCreateModal
          entity={creating}
          saving={store.saving}
          onClose={() => setCreating(null)}
          onCreate={async (body) => {
            const entity = creating;
            const id = await store.createRecord(entity, body);
            setCreating(null);
            if (id) go(openRecord(view, PARAM_FOR[entity], id));
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
  );
}

/**
 * The gate (WS-53 CRM-U5, CRM-Z7). It reads the mode and the connection,
 * shows the first-open cards when no mode exists, handles the return from
 * Zoho, and hands the pipeline its context. The pipeline loads nothing until
 * the gate says "ready".
 */
function CrmGate(): ReactNode {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const toast = useToast();
  const { access, loading: accessLoading } = useAccess();
  const isAdmin = hasCapability(access, ADMIN_CAPABILITY);

  const { phase, mode, connection } = useCrmMode(isAdmin, accessLoading);
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
    const rest = clearReturnParams(search);
    router.replace(rest ? `${pathname}?${rest}` : pathname, { scroll: false });
  }, [search, pathname, router, toast]);

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
    // The "New lead" job lands here too, and it must say why nothing opens.
    const refusal = refusalOf(phase);
    return (
      <>
        {refusal && (
          <ShellJob
            id="new-lead"
            onOpen={() =>
              toast.show({ key: "crm-new-lead", variant: "error", title: refusal })
            }
          />
        )}
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
