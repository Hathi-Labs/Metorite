"use client";

// ── The CRM's client store ────────────────────────────────────────────────
//
// zustand, like the tasks and email apps. Deliberately thin: the view state
// lives in the URL (urlState.ts) and the board's geometry is pure
// (board.ts) — what is left here is the server data, the in-flight flags, and
// the one refusal banner.
//
// Rule this store keeps: a refusal must SURFACE and the list must re-read
// (workbench/AGENTS.md — "a control that silently no-ops reads as broken, and
// a stale row after a 409 reads as success"). So every write path sets
// `error` on failure and re-fetches on every response, refusals included.

import { create } from "zustand";
import * as api from "./api";
import { CrmError } from "./api";
import {
  applyLeadMove,
  applyMove,
  toBoardLanes,
  toLeadLanes,
  type BoardLane,
  type DealMove,
  type LeadLane,
} from "./board";
import { MIRROR_REFUSALS } from "./connect";
import { NO_RELATED, RELATED_PAGE, type RelatedLists } from "./related";
import { exportQuery, extraQuery, listQuery } from "./filters";
import { cardSortQuery, type CardSort } from "./nextActivity";
import {
  applyPatches,
  reorderFailureMessage,
  type PositionPatch,
} from "./settings";
import { isBoardView, isEntity, type CrmView } from "./urlState";
import type {
  Contact,
  CrmReports,
  Deal,
  DealContact,
  EntitySlug,
  Lead,
  LostReason,
  Organization,
  StageMetadataReport,
  Status,
  StatusKind,
  TimelineEntry,
} from "./types";

type Rows = Record<string, unknown>[];

/** The record whose related lists were asked for last. */
let latestRelated = "";

type CrmState = {
  lanes: BoardLane[];
  /** The Leads board: one lane per Lead Status (CRM-Q3). */
  leadLanes: LeadLane[];
  related: RelatedLists;
  rows: Rows;
  total: number;
  dealStatuses: Status[];
  leadStatuses: Status[];
  lostReasons: LostReason[];
  /** Loaded lazily for the convert modal's match resolution. */
  contacts: Contact[];
  organizations: Organization[];
  record: Record<string, unknown> | null;
  timeline: TimelineEntry[];
  dealContacts: DealContact[];
  /** The four WS-26g report blocks, or null before the tab has been opened. */
  reports: CrmReports | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  /**
   * The view the visible collection was loaded for.
   *
   * Kept so a write can re-read WHAT IS ON SCREEN without every caller
   * threading the view back in. A created record that is invisible until
   * somebody hits refresh, or a renamed one whose row still shows the old
   * name, is the same lie as a stale row after a 409 — the store's rule at
   * the top of this file is one rule, and it applies to the successes too.
   */
  lastView: CrmView | null;

  setError: (message: string | null) => void;
  /** Re-read whichever collection is showing (board or list). */
  refreshCollection: () => Promise<void>;
  loadVocabulary: () => Promise<void>;
  /** `cardSort` orders the cards of each deal lane (CRM-X3). */
  loadBoard: (view: CrmView & { cardSort?: CardSort }) => Promise<void>;
  loadLeadBoard: (view: CrmView) => Promise<void>;
  /** A lead dropped on a Lead Status lane. `move.dealId` holds the lead id. */
  moveLead: (move: DealMove, extra?: Record<string, unknown>) => Promise<void>;
  /** WS-26g — the reports tab's read. All four blocks, in one pass. */
  loadReports: (view: CrmView) => Promise<void>;
  loadList: (entity: EntitySlug, view: CrmView) => Promise<void>;
  /**
   * WS-26i-export — the visible list, as a CSV.
   *
   * A read, so it does not touch `saving`; but it DOES surface its refusal
   * through the same banner every write uses, because the useful answer here
   * is a refusal: the gateway answers 422 naming the matched count when the
   * filter is wider than the export cap, and a download button that silently
   * did nothing would read as broken.
   */
  exportList: (entity: EntitySlug, view: CrmView) => Promise<void>;
  loadRecord: (entity: EntitySlug, id: string) => Promise<void>;
  loadRelated: (entity: EntitySlug, id: string) => Promise<void>;
  loadDirectories: () => Promise<void>;
  moveDeal: (move: DealMove, extra?: Record<string, unknown>) => Promise<void>;
  patchRecord: (
    entity: EntitySlug,
    id: string,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  createRecord: (
    entity: EntitySlug,
    body: Record<string, unknown>
  ) => Promise<string | null>;
  logActivity: (
    entity: EntitySlug,
    id: string,
    body: Parameters<typeof api.logActivity>[2]
  ) => Promise<void>;
  toggleTask: (
    activityId: string,
    completed: boolean,
    entity: EntitySlug,
    id: string
  ) => Promise<void>;
  convertLead: (
    leadId: string,
    body: Record<string, unknown>
  ) => Promise<string | null>;
  setPrimaryContact: (dealId: string, contactId: string) => Promise<void>;
  detachContact: (dealId: string, contactId: string) => Promise<void>;

  // ── Pipeline settings (WS-26f f2) ───────────────────────────────────────
  //
  // The settings tab's collection IS the vocabulary, so `loadVocabulary()` is
  // its re-read — and the store's one rule applies here identically: every
  // write re-reads on EVERY response, refusals included. A lane that stays on
  // screen after a 409 ("this status still holds 4 deals") reads as deleted.
  saveStatus: (
    kind: StatusKind,
    statusId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  removeStatus: (kind: StatusKind, statusId: string) => Promise<boolean>;
  reorderStatuses: (
    kind: StatusKind,
    patches: PositionPatch[]
  ) => Promise<void>;
  saveLostReason: (
    reasonId: string | null,
    body: Record<string, unknown>
  ) => Promise<boolean>;
  removeLostReason: (reasonId: string) => Promise<boolean>;
  reorderLostReasons: (patches: PositionPatch[]) => Promise<void>;
  /** `apply` is the owner gate — see api.importZohoStages. */
  pullZohoStages: (apply: boolean) => Promise<StageMetadataReport | null>;
};

/**
 * Where a mirror-guard refusal goes (WS-53 CRM-Z7). The page registers a
 * toast here. With no sink the refusal reads in the banner, as every other
 * refusal does.
 */
let refusalSink: ((text: string) => void) | null = null;
export function setRefusalSink(sink: ((text: string) => void) | null): void {
  refusalSink = sink;
}

/**
 * The words for a failure, or `null` when the toast said them already.
 * A 409 `mirrored_record` is a rule of the mirror and not a fault, so it
 * leaves no red banner behind.
 */
function message(err: unknown): string | null {
  if (err instanceof CrmError) {
    if (refusalSink && err.code && MIRROR_REFUSALS.has(err.code)) {
      refusalSink(err.message);
      return null;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

export const useCrmStore = create<CrmState>((set, get) => ({
  lanes: [],
  leadLanes: [],
  related: NO_RELATED,
  rows: [],
  total: 0,
  dealStatuses: [],
  leadStatuses: [],
  lostReasons: [],
  contacts: [],
  organizations: [],
  record: null,
  timeline: [],
  dealContacts: [],
  reports: null,
  loading: false,
  saving: false,
  error: null,
  lastView: null,

  setError: (error) => set({ error }),

  async refreshCollection() {
    const view = get().lastView;
    if (!view) return;
    if (isBoardView(view)) {
      if (view.tab === "leads") await get().loadLeadBoard(view);
      else await get().loadBoard(view);
    }
    // The reports tab has no writes of its own, but the record sheet opens
    // OVER it (`?tab=reports&deal=<id>`) and moving a deal from there changes
    // every number behind the sheet. The store's one rule — a write re-reads
    // what is on screen — does not stop applying because the surface is a
    // chart.
    else if (view.tab === "reports") await get().loadReports(view);
    // `settings` is a tab with no collection behind it — its own writes
    // re-read the vocabulary directly. Falling through to `loadList` would
    // request `/crm/settings`.
    else if (isEntity(view.tab)) await get().loadList(view.tab, view);
  },

  async loadVocabulary() {
    try {
      const [dealStatuses, leadStatuses, lostReasons] = await Promise.all([
        api.listStatuses("deal"),
        api.listStatuses("lead"),
        api.listLostReasons(),
      ]);
      set({ dealStatuses, leadStatuses, lostReasons });
    } catch (err) {
      set({ error: message(err) });
    }
  },

  async loadBoard(view) {
    // `lastView` keeps `cardSort` too, so a re-read after a write keeps the
    // member's lane order (CRM-X3).
    set({ loading: true, lastView: view });
    try {
      const extra = { ...extraQuery("deals", view), ...cardSortQuery(view.cardSort) };
      const pipeline =
        Object.keys(extra).length > 0
          ? await api.getPipeline(view.owner, view.pipelineId, extra)
          : await api.getPipeline(view.owner, view.pipelineId);
      set({ lanes: toBoardLanes(pipeline), error: null });
    } catch (err) {
      set({ error: message(err) });
    } finally {
      set({ loading: false });
    }
  },

  async loadLeadBoard(view) {
    set({ loading: true, lastView: view });
    try {
      // The statuses come from the vocabulary when it is loaded, and from the
      // gateway when it is not. One page of leads for each status: the lane
      // count is the status total, as on the deals board.
      const statuses =
        get().leadStatuses.length > 0 ? get().leadStatuses : await api.listStatuses("lead");
      const pages = await Promise.all(
        statuses.map((status) =>
          api.listRecords<Lead>("leads", {
            status_id: status.id,
            page_size: 50,
            ...(view.owner ? { owner: view.owner } : {}),
            ...extraQuery("leads", view),
          }),
        ),
      );
      const byStatus: Record<string, { rows: Lead[]; total: number }> = {};
      statuses.forEach((status, i) => {
        byStatus[status.id] = pages[i];
      });
      set({ leadLanes: toLeadLanes(statuses, byStatus), error: null });
    } catch (err) {
      set({ error: message(err), leadLanes: [] });
    } finally {
      set({ loading: false });
    }
  },

  async moveLead(move, extra = {}) {
    // Optimistic, like the deals board. A refusal puts the card back.
    const previous = get().leadLanes;
    set({ leadLanes: applyLeadMove(previous, move), saving: true, error: null });
    try {
      await api.patchRecord("leads", move.dealId, { status_id: move.toStatusId, ...extra });
      await get().refreshCollection();
    } catch (err) {
      set({ leadLanes: previous, error: message(err) });
    } finally {
      set({ saving: false });
    }
  },

  async loadReports(view) {
    // `lastView` like every other loader: `refreshCollection()` reads it to
    // decide what is on screen, and a tab that does not record itself there
    // is one a write silently fails to refresh.
    set({ loading: true, lastView: view });
    try {
      // All four together: they are four independent GETs and the tab shows
      // them on one screen, so serialising them would make the page assemble
      // itself in front of the reader. `Promise.all` also means a single
      // refusal surfaces once, with its own status, rather than four times.
      const [pipeline, funnel, winLoss, owners] = await Promise.all([
        api.getPipelineReport(),
        api.getFunnelReport(),
        api.getWinLossReport(),
        api.getOwnerLeaderboard(),
      ]);
      set({ reports: { pipeline, funnel, winLoss, owners }, error: null });
    } catch (err) {
      // The stale payload is dropped rather than left on screen: a report that
      // keeps rendering last week's numbers under a failed refresh is the same
      // lie as a stale row after a 409, and it is worse here because nothing
      // about a chart says how old it is.
      set({ error: message(err), reports: null });
    } finally {
      set({ loading: false });
    }
  },

  async loadList(entity, view) {
    set({ loading: true, lastView: view });
    try {
      const page = await api.listRecords(entity, listQuery(entity, view));
      set({ rows: page.rows, total: page.total, error: null });
    } catch (err) {
      set({ error: message(err), rows: [], total: 0 });
    } finally {
      set({ loading: false });
    }
  },

  async exportList(entity, view) {
    set({ error: null });
    try {
      await api.exportRecords(entity, exportQuery(entity, view));
    } catch (err) {
      set({ error: message(err) });
    }
  },

  async loadRecord(entity, id) {
    set({ record: null, timeline: [], dealContacts: [], related: NO_RELATED });
    try {
      const [record, timeline] = await Promise.all([
        api.getRecord<Record<string, unknown>>(entity, id),
        api.getTimeline(entity, id),
      ]);
      set({ record, timeline: timeline.entries, error: null });
      if (entity === "deals") {
        const people = await api.listDealContacts(id);
        set({ dealContacts: people.rows });
      }
    } catch (err) {
      set({ error: message(err) });
    }
    await get().loadRelated(entity, id);
  },

  async loadRelated(entity, id) {
    // A company shows its contacts and deals. A contact shows its deals. Both
    // are the existing list routes with a filter, so a related row is the same
    // row the list shows.
    //
    // The latest request wins. Open company A and then B fast, and A's slow
    // answer must not overwrite B's lists.
    const asked = `${entity}:${id}`;
    latestRelated = asked;
    const page = (target: EntitySlug, query: Parameters<typeof api.listRecords>[1]) =>
      api.listRecords(target, { page_size: RELATED_PAGE, ...query });
    try {
      let next: RelatedLists | null = null;
      if (entity === "organizations") {
        const [contacts, deals] = await Promise.all([
          page("contacts", { company_id: id }),
          page("deals", { company_id: id }),
        ]);
        next = {
          ...NO_RELATED,
          contacts: contacts.rows,
          contactsTotal: contacts.total,
          deals: deals.rows,
          dealsTotal: deals.total,
        };
      } else if (entity === "contacts") {
        const deals = await page("deals", { contact_id: id });
        next = { ...NO_RELATED, deals: deals.rows, dealsTotal: deals.total };
      }
      if (next && latestRelated === asked) set({ related: next });
    } catch {
      // A failed read says so, with a Retry. An empty section would read as
      // "no contacts".
      if (latestRelated === asked) set({ related: { ...NO_RELATED, failed: true } });
    }
  },

  async loadDirectories() {
    // The convert modal needs enough of the directory to show the match the
    // server would make. Capped at a page: this is a picker, not a mirror.
    try {
      const [contacts, organizations] = await Promise.all([
        api.listRecords<Contact>("contacts", { page_size: 100 }),
        api.listRecords<Organization>("organizations", { page_size: 100 }),
      ]);
      set({ contacts: contacts.rows, organizations: organizations.rows });
    } catch (err) {
      set({ error: message(err) });
    }
  },

  async moveDeal(move, extra = {}) {
    // Optimistic: a card that snaps back for 300ms while a request flies
    // reads as a bug. The previous lanes are kept so a refusal can undo it.
    const previous = get().lanes;
    set({ lanes: applyMove(previous, move), saving: true, error: null });
    try {
      await api.moveDeal(move, extra);
      // Re-read through the SAME seam every other write uses, rather than a
      // hard-coded pipeline fetch.
      //
      // A moved deal changes whatever surface is showing, and only
      // `refreshCollection()` knows which that is. The hard-coded
      // `api.getPipeline` here re-read the board *whatever* the active tab
      // was, which meant: from `?tab=reports&deal=<id>` the status pill moved
      // the deal to Closed Won and all four report blocks kept their old
      // numbers indefinitely, while the fetched pipeline landed in `lanes` —
      // state that tab does not render. Board and reports then disagreed on
      // one screen. (A lead's status change went through `patchRecord` and
      // DID refresh, so the two entities behaved differently for no reason a
      // reader could see.)
      //
      // `loadBoard` issues exactly the request this used to — `getPipeline`
      // under `view.owner` — so the board keeps its owner scope and does not
      // silently widen to the whole pipeline. It also swallows its own error,
      // which is why a failed RE-READ no longer reverts a move that the
      // server accepted; only a failed PATCH reaches the catch below.
      await get().refreshCollection();
    } catch (err) {
      // Put the card back where it was AND say why. Either alone is a lie.
      set({ lanes: previous, error: message(err) });
    } finally {
      set({ saving: false });
    }
  },

  async patchRecord(entity, id, body) {
    set({ saving: true, error: null });
    try {
      const record = await api.patchRecord<Record<string, unknown>>(
        entity,
        id,
        body
      );
      set({ record });
      // The sheet is open OVER a list, and the list is showing the row that
      // was just edited: renaming a deal and watching the card behind the
      // sheet keep the old name is the same class of lie as a stale row
      // after a 409. Re-read on success too, not only on refusal.
      await get().refreshCollection();
      return true;
    } catch (err) {
      set({ error: message(err) });
      // Re-read on EVERY response, refusals included: a stale row after a 409
      // reads as success.
      await get().loadRecord(entity, id);
      return false;
    } finally {
      set({ saving: false });
    }
  },

  async createRecord(entity, body) {
    set({ saving: true, error: null });
    try {
      const created = await api.createRecord<{ id: string }>(entity, body);
      // Nothing else re-fetches the collection: opening the new record's
      // sheet changes `view.record`, and the load effect keys on the tab and
      // the filters. Without this the record exists on the server and is
      // absent from the list behind the sheet until somebody hits refresh.
      await get().refreshCollection();
      return created.id;
    } catch (err) {
      set({ error: message(err) });
      return null;
    } finally {
      set({ saving: false });
    }
  },

  async logActivity(entity, id, body) {
    set({ saving: true, error: null });
    try {
      await api.logActivity(entity, id, body);
    } catch (err) {
      set({ error: message(err) });
    } finally {
      set({ saving: false });
      const timeline = await api.getTimeline(entity, id).catch(() => null);
      if (timeline) set({ timeline: timeline.entries });
    }
  },

  async toggleTask(activityId, completed, entity, id) {
    try {
      await api.completeTask(activityId, completed);
    } catch (err) {
      set({ error: message(err) });
    }
    const timeline = await api.getTimeline(entity, id).catch(() => null);
    if (timeline) set({ timeline: timeline.entries });
  },

  async convertLead(leadId, body) {
    set({ saving: true, error: null });
    try {
      const result = await api.convertLead(leadId, body);
      return result.deal.id;
    } catch (err) {
      set({ error: message(err) });
      return null;
    } finally {
      set({ saving: false });
    }
  },

  async setPrimaryContact(dealId, contactId) {
    set({ saving: true, error: null });
    try {
      await api.addDealContact(dealId, {
        contact_id: contactId,
        is_primary: true,
      });
    } catch (err) {
      set({ error: message(err) });
    } finally {
      set({ saving: false });
      const people = await api.listDealContacts(dealId).catch(() => null);
      if (people) set({ dealContacts: people.rows });
    }
  },

  async detachContact(dealId, contactId) {
    set({ saving: true, error: null });
    try {
      await api.removeDealContact(dealId, contactId);
    } catch (err) {
      set({ error: message(err) });
    } finally {
      set({ saving: false });
      const people = await api.listDealContacts(dealId).catch(() => null);
      if (people) set({ dealContacts: people.rows });
    }
  },

  // ── Pipeline settings ───────────────────────────────────────────────────

  async saveStatus(kind, statusId, body) {
    set({ saving: true, error: null });
    try {
      if (statusId) await api.patchStatus(kind, statusId, body);
      else await api.createStatus(kind, body);
      return true;
    } catch (err) {
      set({ error: message(err) });
      return false;
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async removeStatus(kind, statusId) {
    set({ saving: true, error: null });
    try {
      await api.deleteStatus(kind, statusId);
      return true;
    } catch (err) {
      // The 409 here is the useful one — "this status still holds 4 deals" —
      // and it is only useful if the lane is still on screen to read it next
      // to, which is what the re-read below guarantees.
      set({ error: message(err) });
      return false;
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async reorderStatuses(kind, patches) {
    set({ saving: true, error: null });
    try {
      // Every patch is issued even after one fails (`applyPatches`): stopping
      // at the first refusal leaves the rows already written holding their new
      // numbers and the rest their old ones, i.e. duplicate positions.
      const failures = await applyPatches(patches, (patch) =>
        api.patchStatus(kind, patch.id, { position: patch.position })
      );
      if (failures.length) {
        set({ error: reorderFailureMessage(failures, patches.length) });
      }
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async saveLostReason(reasonId, body) {
    set({ saving: true, error: null });
    try {
      if (reasonId) await api.patchLostReason(reasonId, body);
      else await api.createLostReason(body as { label: string });
      return true;
    } catch (err) {
      set({ error: message(err) });
      return false;
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async removeLostReason(reasonId) {
    set({ saving: true, error: null });
    try {
      await api.deleteLostReason(reasonId);
      return true;
    } catch (err) {
      set({ error: message(err) });
      return false;
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async reorderLostReasons(patches) {
    set({ saving: true, error: null });
    try {
      const failures = await applyPatches(patches, (patch) =>
        api.patchLostReason(patch.id, { position: patch.position })
      );
      if (failures.length) {
        set({ error: reorderFailureMessage(failures, patches.length) });
      }
    } finally {
      set({ saving: false });
      await get().loadVocabulary();
    }
  },

  async pullZohoStages(apply) {
    set({ saving: true, error: null });
    try {
      const report = await api.importZohoStages(apply);
      return report;
    } catch (err) {
      set({ error: message(err) });
      return null;
    } finally {
      set({ saving: false });
      // An applied repair rewrites the lanes the rest of the app renders from;
      // a dry run changes nothing and re-reading it costs three GETs.
      if (apply) await get().loadVocabulary();
    }
  },
}));

export type { BoardLane, Deal, Lead };
