"use client";

/**
 * The list of one generic Zoho module (WS-53 CRM-Q6b, `crm_platform.md`
 * section 18.7). Products, Quotes and each custom module that the mirror
 * holds.
 *
 * It is the shared list, not a second one: `RecordList` draws the rows, with
 * the columns of `MODULE_COLUMNS`, the Columns control of `fields.ts` and the
 * "Load more" of CRM-X1. The CRM-X1 search box sits above it and writes
 * `?q=`. Read only, in every mode: the sync is the only writer.
 *
 * The rows of a module are not in the CRM store, so the paging state lives
 * here. It keeps the rules of the store's `loadList` and `loadMore` (CRM-X1):
 * page 1 on a new view, `appendPage` for the next page, a short page ends the
 * list, and an answer for an older view is dropped.
 */

import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { searchChanges } from "../lib/filterBar";
import { appendPage } from "../lib/filters";
import {
  MODULE_COLUMNS,
  MODULE_PAGE_SIZE,
  MODULE_SORTS,
  listRow,
  moduleListQuery,
  type ModuleFieldDef,
} from "../lib/modules";
import { applySort, type CrmView } from "../lib/urlState";
import { SearchBox } from "./FilterBar";
import RecordList from "./RecordList";

type Row = Record<string, unknown>;

type ListState = {
  rows: Row[];
  total: number;
  defs: ModuleFieldDef[] | null;
  page: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
};

const START: ListState = {
  rows: [],
  total: 0,
  defs: null,
  page: 1,
  hasMore: false,
  loading: true,
  loadingMore: false,
  error: null,
};

function message(err: unknown): string {
  return err instanceof CrmError ? err.message : "The list did not load. Try again.";
}

/** Page 1 of the view, then "Load more". An answer for an older view is dropped. */
function useModuleList(module: string, view: CrmView, reloadToken: number) {
  const [state, setState] = useState<ListState>(START);
  const latest = useRef(0);
  const key = `${module}|${view.q}|${view.sort ?? ""}|${view.dir}|${reloadToken}`;

  useEffect(() => {
    const asked = ++latest.current;
    // A new view starts again at page 1, with the rows of the old one gone.
    setState((s) => ({ ...s, loading: true, loadingMore: false, error: null }));
    api
      .listModuleRecords(module, moduleListQuery(view, 1))
      .then((res) => {
        if (asked !== latest.current) return;
        setState({
          ...START,
          rows: res.rows.map(listRow),
          total: res.total,
          defs: res.field_defs ?? null,
          hasMore: res.rows.length >= MODULE_PAGE_SIZE,
          loading: false,
        });
      })
      .catch((err) => {
        if (asked !== latest.current) return;
        setState({ ...START, loading: false, error: message(err) });
      });
    // `key` holds every input of the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  async function loadMore() {
    if (state.loadingMore || !state.hasMore) return;
    const asked = latest.current;
    const next = state.page + 1;
    setState((s) => ({ ...s, loadingMore: true }));
    try {
      const res = await api.listModuleRecords(module, moduleListQuery(view, next));
      if (asked !== latest.current) return;
      setState((s) => ({
        ...s,
        rows: appendPage(s.rows, res.rows.map(listRow)),
        total: res.total,
        page: next,
        // A short page, or an empty one, is the end of the list.
        hasMore: res.rows.length >= MODULE_PAGE_SIZE,
        loadingMore: false,
      }));
    } catch (err) {
      if (asked !== latest.current) return;
      setState((s) => ({ ...s, loadingMore: false, error: message(err) }));
    }
  }

  return { ...state, loadMore };
}

export default function ModuleList({
  module,
  label,
  view,
  onChange,
  onOpen,
  reloadToken = 0,
}: {
  /** The module's API name (`Products`). */
  module: string;
  /** Its plural label, for the search box. */
  label: string;
  view: CrmView;
  onChange: (next: CrmView) => void;
  onOpen: (id: string) => void;
  /** The Refresh button counts it up, and the list reads page 1 again. */
  reloadToken?: number;
}) {
  const list = useModuleList(module, view, reloadToken);
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="shrink-0 border-b border-border px-3 py-2 sm:px-4" data-testid="crm-module-bar">
        <SearchBox
          label={label.toLowerCase()}
          value={view.q}
          onCommit={(q) => {
            if (searchChanges(q, view.q)) onChange({ ...view, q });
          }}
        />
      </div>
      {list.error && (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {list.error}
        </p>
      )}
      <RecordList
        entity={`module:${module}`}
        columns={MODULE_COLUMNS}
        sortKeys={MODULE_SORTS}
        fieldDefs={list.defs}
        rows={list.rows}
        total={list.total}
        loading={list.loading}
        sort={view.sort}
        direction={view.dir}
        onSort={(key) => onChange(applySort(view, key))}
        onOpen={onOpen}
        onLoadMore={() => void list.loadMore()}
        loadingMore={list.loadingMore}
        hasMore={list.hasMore}
      />
    </div>
  );
}
