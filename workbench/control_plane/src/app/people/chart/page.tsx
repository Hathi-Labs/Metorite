"use client";

/**
 * People Center · the org chart (WS-28c).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.4.
 *
 * A collapsible tree from `manager_id`, with search-to-focus. Unmanaged
 * people surface as roots — that is information, not an error state. Each
 * node carries its `org_group` tints (the Center overlay), and where the
 * free-text department names a group the person is not in, the node SAYS so.
 *
 * Re-parenting is drag-to-drop behind `can_manage`, refused client-side when
 * it would close a loop, confirmed by a human, and written through
 * `PATCH /api/people/{id}` — the People router's own class-checked door, NOT
 * the tasks-app one: a chart holder need never hold `feature:tasks`
 * (adversarial review caught the wrong door here).
 */

import { useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";
import { categoricalAccent } from "@/lib/categorical";

import { Avatar } from "../components/Avatar";
import { PeopleApiError, peopleApi } from "../lib/api";
import { PAGE_FRAME, PAGE_FRAME_BLOCK } from "../lib/frame";
import {
  type ChartResponse,
  type TreeNode,
  buildTree,
  departmentMismatch,
  focusIds,
  wouldCycle,
} from "../lib/chart";

interface RowProps {
  tnode: TreeNode;
  searching: boolean;
  focus: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  dragging: string | null;
  canManage: boolean;
  groupSlugs: ReadonlySet<string>;
  onToggle: (id: string) => void;
  onDragState: (id: string | null) => void;
  onDropOn: (moved: string, target: string) => void;
}

/**
 * Module-scope on purpose: declared inside the page it would get a new type
 * identity per render, and React would unmount/rebuild the whole tree DOM on
 * every keystroke — losing keyboard focus and any in-flight drag with it.
 */
function ChartRow(props: RowProps) {
  const { tnode, searching, focus, collapsed, dragging, canManage, groupSlugs } =
    props;
  const n = tnode.node;
  if (searching && !focus.has(n.id)) return null;
  const isCollapsed = !searching && collapsed.has(n.id);
  const mismatch = departmentMismatch(n, groupSlugs);
  return (
    <li>
      <div
        className={`flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 ${
          dragging === n.id ? "opacity-50" : ""
        }`}
        draggable={canManage}
        onDragStart={(e) => {
          props.onDragState(n.id);
          e.dataTransfer.setData("text/plain", n.id);
        }}
        onDragEnd={() => props.onDragState(null)}
        onDragOver={(e) => {
          if (canManage) e.preventDefault();
        }}
        onDrop={(e) => {
          e.preventDefault();
          const moved = e.dataTransfer.getData("text/plain");
          props.onDragState(null);
          if (moved) props.onDropOn(moved, n.id);
        }}
      >
        {tnode.children.length > 0 ? (
          <button
            type="button"
            onClick={() => props.onToggle(n.id)}
            className="text-muted-foreground"
            aria-label={isCollapsed ? "Expand" : "Collapse"}
          >
            <Icon name={isCollapsed ? "ChevronRight" : "ChevronDown"} size={14} />
          </button>
        ) : (
          <span className="w-3.5" />
        )}
        <Avatar name={n.name} avatar={n.avatar} className="size-6 text-[10px]" />
        <div className="min-w-0">
          <p className="truncate text-sm">
            {n.name}
            {tnode.cycle ? (
              <span className="ml-2 text-xs text-destructive">
                manager loop — severed here
              </span>
            ) : null}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {[n.title, n.department].filter(Boolean).join(" · ")}
            {isCollapsed ? ` · ${tnode.children.length} reports` : ""}
          </p>
          {mismatch ? (
            <p className="text-[11px] text-warning">{mismatch}</p>
          ) : null}
        </div>
        <span className="ml-auto flex shrink-0 gap-1">
          {n.groups.map((slug) => (
            <span
              key={slug}
              className={`rounded-full border px-1.5 text-[10px] ${categoricalAccent(slug).chip}`}
            >
              {slug}
            </span>
          ))}
        </span>
      </div>
      {!isCollapsed && tnode.children.length > 0 ? (
        <ul className="ml-5 mt-1 flex flex-col gap-1 border-l border-border pl-3">
          {tnode.children.map((c) => (
            <ChartRow key={c.node.id} {...props} tnode={c} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export default function OrgChartPage() {
  const [res, setRes] = useState<ChartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragging, setDragging] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const out = await peopleApi.chart();
        if (live) setRes(out);
      } catch (err) {
        if (live)
          setError(
            err instanceof PeopleApiError
              ? err.message
              : String((err as Error).message)
          );
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  const tree = useMemo(() => (res ? buildTree(res.nodes) : null), [res]);
  const focus = useMemo(
    () => (res ? focusIds(res.nodes, q) : new Set<string>()),
    [res, q]
  );
  const groupSlugs = useMemo(
    () => new Set((res?.groups ?? []).map((g) => g.slug)),
    [res]
  );

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function reparent(personId: string, managerId: string) {
    if (!res) return;
    if (personId === managerId) return;
    const person = res.nodes.find((n) => n.id === personId);
    const manager = res.nodes.find((n) => n.id === managerId);
    if (!person || !manager) return;
    if (wouldCycle(res.nodes, personId, managerId)) {
      window.alert(
        `${manager.name} reports up through ${person.name} — that move would close a loop.`
      );
      return;
    }
    if (!window.confirm(`Move ${person.name} under ${manager.name}?`)) {
      return;
    }
    try {
      await peopleApi.update(personId, { manager_id: managerId });
      // A failed REFRESH after a successful write must not blank the chart:
      // the write landed, the alert says the view is stale, the tree stays.
      try {
        setRes(await peopleApi.chart());
      } catch {
        window.alert("Saved, but refreshing the chart failed — reload to see it.");
      }
    } catch (err) {
      window.alert(
        err instanceof PeopleApiError
          ? err.message
          : String((err as Error).message)
      );
    }
  }

  if (error && !res) {
    return (
      <main className={PAGE_FRAME_BLOCK}>
        <p className="text-sm text-muted-foreground">{error}</p>
      </main>
    );
  }
  if (!res || !tree) {
    return (
      <main className={PAGE_FRAME_BLOCK}>
        <p className="text-sm text-muted-foreground">Drawing the chart…</p>
      </main>
    );
  }

  return (
    <main className={PAGE_FRAME}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <Icon name="Network" size={20} />
            Org chart
          </h1>
          <p className="text-xs text-muted-foreground">
            From each person&apos;s recorded manager. Unmanaged people appear
            as roots{res.can_manage ? " · drag a person onto their new manager" : ""}
          </p>
        </div>
      </header>

      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Find a person, title or department…"
        aria-label="Search the chart"
      />

      {tree.cycleIds.length > 0 ? (
        <p className="rounded-md border border-border bg-card p-2 text-xs text-destructive">
          The record contains a manager loop ({tree.cycleIds.length} people).
          The chart severed it to stay drawable — fix the manager on one of
          the flagged rows.
        </p>
      ) : null}

      {res.groups.length > 0 ? (
        <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          Groups:
          {res.groups.map((g) => (
            <span key={g.slug} className="flex items-center gap-1">
              <span
                className={`size-2 rounded-full ${categoricalAccent(g.slug).dot}`}
              />
              {g.display_name}
            </span>
          ))}
        </p>
      ) : null}

      <ul className="flex flex-col gap-1">
        {tree.roots.map((r) => (
          <ChartRow
            key={r.node.id}
            tnode={r}
            searching={q.trim().length > 0}
            focus={focus}
            collapsed={collapsed}
            dragging={dragging}
            canManage={res.can_manage}
            groupSlugs={groupSlugs}
            onToggle={toggle}
            onDragState={setDragging}
            onDropOn={(moved, target) => void reparent(moved, target)}
          />
        ))}
      </ul>
    </main>
  );
}
