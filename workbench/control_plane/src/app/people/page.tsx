"use client";

/**
 * People Center · the directory (§3.1) and the person page (§3.2).
 *
 * Spec: `project-docs/specs/people_center_app.md` · ticket WS-28b.
 *
 * ONE app, like Projects. The People Center links here; so does the Projects
 * assignee picker (WS-28e) once it lands. A person findable in one is findable
 * in the other because both read the same endpoint.
 *
 * Writes are absent rather than disabled: without `admin:members:manage` there
 * is no edit control to grey out, because disabled-button theatre teaches
 * people to hunt for permissions they may never get. The gateway answers that
 * question as `can_manage` on the read, so the page knows before it draws.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import Button from "@/components/ui/Button";

import { AwayBadge } from "./components/AbsencePanel";
import { Avatar } from "./components/Avatar";
import { PersonEditor } from "./components/PersonEditor";
import { PersonPanel } from "./components/PersonPanel";
import { type PersonDetail, type PersonRow, peopleApi } from "./lib/api";
import { DEFAULT_STATUS } from "./lib/form";
import {
  groupByDepartment,
  initials,
  skillsState,
  statusTone,
} from "./lib/directory";

const TONE: Record<string, string> = {
  active: "bg-muted text-foreground",
  warn: "border border-border text-foreground",
  muted: "text-muted-foreground",
};

export default function PeoplePage() {
  const [rows, setRows] = useState<PersonRow[]>([]);
  const [hrVisible, setHrVisible] = useState(true);
  const [canManage, setCanManage] = useState(false);
  const [facets, setFacets] = useState<{
    departments: Array<{ department: string; total: number }>;
    statuses: string[];
  }>({ departments: [], statuses: [] });
  const [q, setQ] = useState("");
  const [department, setDepartment] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /**
   * `undefined` = the editor is closed, `null` = creating, a person = editing.
   * Three states rather than a boolean beside a person, because those two can
   * disagree and "open, editing nobody" would render a create form titled with
   * somebody's name.
   */
  const [editing, setEditing] = useState<PersonDetail | null | undefined>(
    undefined,
  );
  /** Bumped after a save; both the list and the open panel re-read on it. */
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await peopleApi.directory({
          q,
          department: department ?? undefined,
          status: status ?? undefined,
        });
        if (!live) return;
        setRows(res.rows);
        setHrVisible(res.hr_visible);
        setCanManage(res.can_manage);
        setError(null);
      } catch (err) {
        if (live) setError(String((err as Error).message));
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [q, department, status, reloadKey]);

  const onSaved = useCallback(() => setReloadKey((n) => n + 1), []);


  useEffect(() => {
    let live = true;
    peopleApi
      .facets()
      .then((res) => {
        if (live) setFacets({ departments: res.departments, statuses: res.statuses });
      })
      .catch(() => {
        // Filters are an accelerator, not the surface. A directory that fails
        // to render because its facet query failed would be worse than one
        // without filter chips.
      });
    return () => {
      live = false;
    };
  }, []);

  const groups = useMemo(() => groupByDepartment(rows), [rows]);

  return (
    <div className="flex h-full min-h-0">
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-border p-3">
          {/* `flex-wrap`: three controls plus the title squeeze the header
              at 390px — measured in the visual rig. Wrapping is the honest
              answer; compressing each label onto two lines is not. */}
          <div className="mb-2 flex flex-wrap items-baseline gap-2">
            <h1 className="text-sm font-semibold text-foreground">People</h1>
            <span className="text-xs text-muted-foreground">
              {loading ? "loading…" : `${rows.length} in the directory`}
            </span>
            <span className="flex-1" />
            {canManage ? (
              <Button size="sm" icon="Plus" onClick={() => setEditing(null)}>
                Add person
              </Button>
            ) : null}
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={
              hrVisible
                ? "Search name, title, department or skill…"
                : "Search name, title or department…"
            }
            aria-label="Search the directory"
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
          />
          <div className="mt-2 flex flex-wrap gap-1 text-xs">
            <Chip active={department === null} onClick={() => setDepartment(null)}>
              All departments
            </Chip>
            {facets.departments.map((d) => (
              <Chip
                key={d.department}
                active={department === d.department}
                onClick={() => setDepartment(d.department)}
              >
                {d.department} ({d.total})
              </Chip>
            ))}
            <span className="mx-1 w-px bg-border" />
            <Chip active={status === null} onClick={() => setStatus(null)}>
              Any status
            </Chip>
            {facets.statuses.map((s) => (
              <Chip key={s} active={status === s} onClick={() => setStatus(s)}>
                {s}
              </Chip>
            ))}
          </div>
          {!hrVisible ? (
            // Stated once, at the top, rather than as a puzzle repeated on
            // every row: a blank skills strip means "restricted" here, not
            // "nobody filled it in".
            <p className="mt-2 text-xs text-muted-foreground">
              Skills, résumés and capacity are hidden — they need{" "}
              <code>admin:members:read</code>.
            </p>
          ) : null}
        </header>

        {error ? (
          <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
            {error}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {!loading && rows.length === 0 ? (
            /*
              Two different emptinesses, and conflating them was the original
              complaint. With a filter on, "nobody matches" is the truth.
              With no filter at all, the directory itself is empty — which
              since migration 206 means the organization has no active member
              with an address, because membership fills this table by trigger
              and no longer by anybody remembering to.
            */
            q || department || status ? (
              <p className="text-sm text-muted-foreground">Nobody matches that.</p>
            ) : (
              <div className="rounded-xl border border-border p-4">
                <p className="text-sm text-foreground">The directory is empty.</p>
                {/* `max-w-prose`: unbounded, this is a single 1050px line at
                    1440 — a measure nobody reads to the end of. */}
                <p className="mt-1 max-w-prose text-xs text-muted-foreground">
                  Everybody in your organization appears here on their own —
                  a member is added to the directory when they are invited.
                  {canManage
                    ? " Add person is for somebody with no login, such as a contractor or a new hire before day one."
                    : " An administrator can add somebody who has no login."}
                </p>
              </div>
            )
          ) : null}
          {groups.map((group) => (
            <section key={group.department} className="mb-4">
              <h2 className="mb-1 px-1 text-xs font-medium text-foreground">
                {group.department}{" "}
                <span className="text-muted-foreground">({group.people.length})</span>
              </h2>
              {group.people.map((p) => {
                const skills = skillsState(p.skills, hrVisible);
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setOpenId(p.id)}
                    className="flex w-full items-center gap-2 border-b border-border px-1 py-2 text-left last:border-0 hover:bg-muted"
                  >
                    <Avatar name={p.name} avatar={p.avatar} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 truncate text-sm text-foreground">
                        {p.name}
                        <AwayBadge away={p.away} />
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {[p.title || p.role, p.team].filter(Boolean).join(" · ") || "—"}
                      </span>
                    </span>
                    <span className="hidden shrink-0 gap-1 sm:flex">
                      {skills.kind === "restricted" ? (
                        <span className="text-xs text-muted-foreground">restricted</span>
                      ) : skills.kind === "empty" ? (
                        <span className="text-xs text-muted-foreground">no skills yet</span>
                      ) : (
                        <>
                          {skills.shown.map((s) => (
                            <span
                              key={s}
                              className="rounded bg-muted px-1 py-0.5 text-[10px] text-foreground"
                            >
                              {s}
                            </span>
                          ))}
                          {skills.more > 0 ? (
                            <span className="text-[10px] text-muted-foreground">
                              ＋{skills.more}
                            </span>
                          ) : null}
                        </>
                      )}
                    </span>
                    <span
                      className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${TONE[statusTone(p.status)]}`}
                    >
                      {p.status}
                    </span>
                  </button>
                );
              })}
            </section>
          ))}
        </div>
      </main>

      {openId ? (
        <PersonPanel
          personId={openId}
          reloadKey={reloadKey}
          onClose={() => setOpenId(null)}
          onEdit={setEditing}
        />
      ) : null}

      {editing !== undefined ? (
        <PersonEditor
          person={editing}
          hrVisible={hrVisible}
          directory={rows}
          statuses={facets.statuses.length ? facets.statuses : [DEFAULT_STATUS]}
          onSaved={onSaved}
          onClose={() => setEditing(undefined)}
        />
      ) : null}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-2 py-1 ${
        active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted"
      }`}
    >
      {children}
    </button>
  );
}
