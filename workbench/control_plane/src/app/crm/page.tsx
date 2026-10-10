"use client";

/**
 * /crm — the native CRM (spec: project-docs/specs/crm_app.md §5).
 *
 * Views over one dataset: the deals kanban (the landing view) and a list
 * per entity on the shared list contract. Since WS-53 CRM-U1 the app opens
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
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AppTopBar } from "@/components/AppTopBar";
import { useMobileDrawer } from "@/components/AppShell";
import FilterPills from "@/components/FilterPills";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";
import { railClass, useRailFold } from "@/lib/railFold";
import ConvertModal from "./components/ConvertModal";
import CrmRail from "./components/CrmRail";
import KanbanBoard from "./components/KanbanBoard";
import MoveModal from "./components/MoveModal";
import PipelineSettings from "./components/PipelineSettings";
import QuickCreateModal from "./components/QuickCreateModal";
import RecordList from "./components/RecordList";
import Reports from "./components/Reports";
import RecordSheet from "./components/RecordSheet";
import {
  boardTotals,
  missingRequiredFields,
  needsLostReason,
  type DealMove,
  type RequireableField,
} from "./lib/board";
import { activeChip, applyChip, chipsFor } from "./lib/filters";
import { compactMoney } from "./lib/format";
import { useCrmStore } from "./lib/store";
import type { EntitySlug, Lead, Status } from "./lib/types";
import {
  applySort,
  closeRecord,
  isEntity,
  openRecord,
  parseView,
  selectTab,
  viewHref,
  type CrmView,
  type SheetParam,
} from "./lib/urlState";
import { viewLabel } from "./lib/views";

/** Which URL parameter opens a record of each collection. */
const PARAM_FOR: Record<EntitySlug, SheetParam> = {
  deals: "deal",
  leads: "lead",
  contacts: "contact",
  organizations: "organization",
};

function CrmPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const view = useMemo(
    () => parseView(searchParams.toString()),
    [searchParams]
  );

  const store = useCrmStore();
  const { access } = useAccess();
  const { isMobile } = useViewMode();
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

  useEffect(() => {
    store.loadVocabulary();
    // Loaded once: the vocabulary is small and every tab needs it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (view.tab === "board") store.loadBoard(view);
    // ⚠️ `settings` and `reports` are tabs and NOT collections:
    // `loadList("settings", …)` would request `/crm/settings` and empty the
    // screen with a 404. The vocabulary settings edits is loaded once, above;
    // reports has four reads of its own.
    else if (view.tab === "reports") store.loadReports(view);
    else if (isEntity(view.tab)) store.loadList(view.tab, view);
    // ⚠️ Every field `listQuery` reads has to be in this list, `sort`/`dir`
    // included: a filter the effect does not watch is a control that changes
    // its own appearance and issues no request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    view.tab,
    view.q,
    view.statusId,
    view.includeConverted,
    view.owner,
    view.sort,
    view.dir,
  ]);

  useEffect(() => {
    if (view.record) store.loadRecord(view.record.entity, view.record.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.record?.entity, view.record?.id]);

  const listEntity: EntitySlug | null = isEntity(view.tab) ? view.tab : null;

  /** What the Refresh button re-reads — one branch per kind of tab. */
  function reload(): void {
    if (view.tab === "board") void store.loadBoard(view);
    // The settings tab's collection IS the vocabulary the whole app renders
    // from, so its re-read is the same call the page makes on mount.
    else if (view.tab === "settings") void store.loadVocabulary();
    else if (view.tab === "reports") void store.loadReports(view);
    else if (listEntity) void store.loadList(listEntity, view);
  }
  const statusesFor = (entity: EntitySlug | null): Status[] =>
    entity === "deals"
      ? store.dealStatuses
      : entity === "leads"
        ? store.leadStatuses
        : [];

  const chips = listEntity
    ? chipsFor(listEntity, statusesFor(listEntity), view)
    : [];

  const totals = boardTotals(store.lanes);

  /** A board drop, or the status pill in the sheet. Same request either way. */
  async function moveDeal(move: DealMove, target: Status) {
    const deal =
      store.lanes.flatMap((l) => l.rows).find((d) => d.id === move.dealId) ??
      (store.record as Record<string, unknown> | null);
    // Both gates read forward, in the gateway's own order. It refuses either
    // one BEFORE writing any of the transition's three effects, so a board
    // that discovered them from an error toast would have already animated the
    // card into the lane it is not allowed to be in.
    const needsReason = Boolean(deal && needsLostReason(deal, target));
    const missing = deal ? missingRequiredFields(deal, target) : [];
    if (needsReason || missing.length > 0) {
      // The picker the organization requirement needs. Requested here rather
      // than on mount because the directory is a picker, not a mirror — and
      // unconditionally rather than only for that one field, because the call
      // is idempotent and a branch on which field is missing is a branch that
      // gets it wrong once.
      store.loadDirectories();
      setPendingMove({ move, status: target, needsReason, missing });
      return;
    }
    await store.moveDeal(move);
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
  const subtitle =
    view.tab === "board"
      ? // Weighted sits next to the raw total rather than replacing it:
        // "₹4.2Cr in the pipeline" is what is on the table and "₹1.6Cr
        // weighted" is what the forecast believes, and a line that showed
        // only one of them would be answering a different question.
        `${totals.count} open deals · ${compactMoney(totals.amount)} in the pipeline · ${compactMoney(totals.weighted)} weighted`
      : view.tab === "settings"
        ? "Stages, statuses and lost reasons — the pipeline is data, not a deploy"
        : view.tab === "reports"
          ? "Forecast, funnel, win rate and who is carrying what"
          : "Pipeline, leads and customers";

  /** A rail row, from the desktop column or the phone's drawer. */
  function chooseView(tab: CrmView["tab"]): void {
    go(selectTab(view, tab));
    if (isMobile) closeDrawer();
  }

  // The app's verb sits next to its name, as Capture does in My Tasks. It is
  // the quick-create entry the old header held, at the bar's size.
  const actions =
    view.tab !== "settings" && view.tab !== "reports" ? (
      <Button
        variant="secondary"
        size="sm"
        icon="Plus"
        onClick={() => setCreating(listEntity ?? "deals")}
      >
        New
      </Button>
    ) : null;

  const tools = (
    <>
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
            await store.exportList(listEntity, view);
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
                    <CrmRail tab={view.tab} onSelect={chooseView} />
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
            <CrmRail tab={view.tab} onSelect={chooseView} />
          </nav>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {chips.length > 0 && (
            <FilterPills
              items={chips}
              activeId={activeChip(view)}
              onChange={(id) => go(applyChip(view, id))}
            />
          )}

          {store.error && (
            // A refusal must surface: a control that silently no-ops reads as
            // broken, and a stale row after a 409 reads as success.
            <div className="flex shrink-0 items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-4 py-2">
              <p className="flex-1 text-xs text-destructive">{store.error}</p>
              <button
                onClick={() => store.setError(null)}
                className="text-destructive/70 hover:text-destructive"
                aria-label="Dismiss"
              >
                <Icon name="X" className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {view.tab === "board" ? (
            <KanbanBoard
              lanes={store.lanes}
              loading={store.loading}
              onMove={moveDeal}
              onOpen={(id) => go(openRecord(view, "deal", id))}
              onCreate={() => setCreating("deals")}
            />
          ) : view.tab === "reports" ? (
            <Reports reports={store.reports} loading={store.loading} />
          ) : view.tab === "settings" ? (
            <PipelineSettings
              dealStatuses={store.dealStatuses}
              leadStatuses={store.leadStatuses}
              lostReasons={store.lostReasons}
              saving={store.saving}
              // Hiding the pull is the courtesy half; the route is floored on the
              // same capability server-side and refuses regardless.
              canPullStages={hasCapability(access, "admin:access:manage")}
              onSaveStatus={store.saveStatus}
              onRemoveStatus={store.removeStatus}
              onReorderStatuses={store.reorderStatuses}
              onSaveLostReason={store.saveLostReason}
              onRemoveLostReason={store.removeLostReason}
              onReorderLostReasons={store.reorderLostReasons}
              onPullStages={store.pullZohoStages}
            />
          ) : (
            // `listEntity` is non-null here by construction — the three branches
            // above are the only tabs that are not a collection — but it is read
            // through the guard rather than cast, because the cast is what made
            // `?tab=settings` render a list of nothing on the way in.
            listEntity && (
              <RecordList
                entity={listEntity}
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
        </main>
      </div>

      {view.record && (
        <RecordSheet
          entity={view.record.entity}
          record={store.record}
          statuses={statusesFor(view.record.entity)}
          timeline={store.timeline}
          dealContacts={store.dealContacts}
          saving={store.saving}
          onClose={() => go(closeRecord(view))}
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
        />
      )}

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
            await store.moveDeal(move, payload);
            if (view.record) store.loadRecord(view.record.entity, view.record.id);
          }}
        />
      )}
    </div>
  );
}

export default function CrmPage() {
  return (
    <Suspense fallback={null}>
      <CrmPageInner />
    </Suspense>
  );
}
