"use client";

/**
 * The list of one generic Zoho module (WS-53 CRM-Q6b, `crm_platform.md`
 * section 18.7). Products, Quotes and each custom module that the mirror
 * holds.
 *
 * It is the shared list, not a second one: `RecordList` draws the rows, with
 * the columns of `MODULE_COLUMNS` and the Columns control of `fields.ts`. A
 * search box above it writes `?q=`, and "Load more" adds the next page under
 * the rows. Read only, in every mode: the sync is the only writer.
 *
 * ⚠️ The paging is local to this view, with the names and the rules of the
 * CRM-X1 store (`page`, `hasMore`, `loadMore`, latest wins). When CRM-X1 is on
 * main, the search box and "Load more" move onto its shared parts.
 */

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { filterWord } from "@/lib/shell/registry";
import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import {
  MODULE_COLUMNS,
  MODULE_PAGE_SIZE,
  MODULE_SORTS,
  appendRows,
  listRow,
  moduleListQuery,
  type ModuleFieldDef,
} from "../lib/modules";
import { applySort, type CrmView } from "../lib/urlState";
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
        rows: appendRows(s.rows, res.rows.map(listRow)),
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
  const showMore = !list.loading && list.hasMore && list.rows.length > 0 && list.rows.length < list.total;
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="shrink-0 border-b border-border px-3 py-2 sm:px-4" data-testid="crm-module-bar">
        <SearchBox
          label={label.toLowerCase()}
          value={view.q}
          onCommit={(q) => {
            if (q !== view.q.trim()) onChange({ ...view, q });
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
        more={
          showMore ? (
            <div className="flex justify-center p-3">
              <Button
                variant="secondary"
                size="sm"
                loading={list.loadingMore}
                onClick={() => void list.loadMore()}
              >
                Load more
              </Button>
            </div>
          ) : null
        }
      />
    </div>
  );
}

/**
 * The search box. It writes `?q=` once, 300 ms after the last key, so each
 * search is one history entry. A `?q=` that changes from outside (Back, a
 * link) replaces the text. `data-page-filter` lets the shell's `/` reach it.
 */
function SearchBox({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string;
  onCommit: (q: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return (
    <Input
      icon="Search"
      inputSize="sm"
      className="w-56"
      value={draft}
      onChange={(e) => {
        const text = e.target.value;
        setDraft(text);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => onCommit(text.trim()), 300);
      }}
      placeholder={`${filterWord()} ${label}…`}
      aria-label={`${filterWord()} ${label}`}
      data-page-filter={label}
    />
  );
}
