"use client";

/**
 * People Center · the person page (§5.2) — six panels.
 *
 * Identity, skills, capacity, work — deliberately in that order: who they are,
 * what they can do, how loaded they are, what they are holding — and then the
 * two WS-28g added: the profile they wrote about themselves, and the
 * employment record the organisation keeps.
 *
 * The last two are `ProfilePanels`, the SAME component `/people/me` renders.
 * One component, two entry points, so a field added to one cannot be missing
 * from the other — and the edit controls appear here for exactly the fields the
 * server put in `editable_fields`, which on a colleague's page (for an admin)
 * is a different set than on your own.
 */
import Link from "next/link";
import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";

import { type PersonDetail, type WorkRow, peopleApi } from "../lib/api";
import { initials, loadBar, skillOrigin, statusTone } from "../lib/directory";
import { describeSkill } from "../lib/skills";
import { AbsencePanel, AwayBadge } from "./AbsencePanel";
import { Avatar } from "./Avatar";
import { ProfilePanels } from "./ProfilePanels";

interface Props {
  personId: string;
  /**
   * Where this is being shown.
   *
   * `panel` is the directory's side column. `page` is `/people/{id}`, a
   * surface with its own URL — which is what lets the org chart, a search
   * result and a colleague's message all point at the same person (H-145).
   *
   * One component, two presentations, for the reason this file already gives
   * about `ProfilePanels`: a field added to one must not be missing from the
   * other. The variant swaps the frame and drops the close control, and
   * touches nothing else.
   */
  variant?: "panel" | "page";
  /** Bumped by the page after a save, so the panel re-reads what was written. */
  reloadKey?: number;
  /** Required for the panel. The page has the back button instead. */
  onClose?: () => void;
  /**
   * Opens the editor on this person. The panel raises it rather than rendering
   * the editor itself: the editor needs the directory (for the manager picker)
   * and the status vocabulary, and both belong to the page.
   */
  onEdit?: (person: PersonDetail) => void;
}

const TONE: Record<string, string> = {
  active: "bg-muted text-foreground",
  warn: "border border-border text-foreground",
  muted: "text-muted-foreground",
};

export function PersonPanel({
  personId,
  variant = "panel",
  reloadKey = 0,
  onClose,
  onEdit,
}: Props) {
  const [person, setPerson] = useState<PersonDetail | null>(null);
  const [work, setWork] = useState<{ rows: WorkRow[]; available: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [detail, tasks] = await Promise.all([
          peopleApi.person(personId),
          peopleApi.work(personId),
        ]);
        if (!live) return;
        setPerson(detail);
        setWork({ rows: tasks.rows, available: tasks.available });
        setError(null);
      } catch (err) {
        if (live) setError(String((err as Error).message));
      }
    })();
    return () => {
      live = false;
    };
  }, [personId, reloadKey]);

  // Resolved BEFORE the early returns: a loading or error state on the page
  // must not render as a side panel either, which is what made this a bug
  // the first time round.
  const asPage = variant === "page";
  const Frame = asPage ? "div" : "aside";
  const shellClass = asPage
    ? "flex w-full flex-col p-3"
    : "flex h-full w-full max-w-md flex-col border-l border-border bg-card p-3";

  if (error) {
    return (
      <Frame className={shellClass}>
        <p className="text-sm text-foreground">{error}</p>
      </Frame>
    );
  }
  if (!person) {
    return (
      <Frame className={shellClass}>
        <p className="text-sm text-muted-foreground">Loading…</p>
      </Frame>
    );
  }

  const bar = loadBar(person);

  return (
    <Frame
      className={
        asPage
          ? "flex w-full flex-col"
          : "flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-border bg-card"
      }
    >
      <header className="flex items-start justify-between gap-2 border-b border-border p-3">
        <div className="flex min-w-0 items-center gap-2">
          <Avatar name={person.name} avatar={person.avatar}
                  className="size-9 text-xs" />
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 truncate text-sm font-medium text-foreground">
              {person.name}
              <AwayBadge away={person.away} />
            </h2>
            <p className="truncate text-xs text-muted-foreground">
              {person.title || person.role || "—"}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {/* WS-28e §6.4 — "Assign work" routes through the ORDINARY
              task-create flow: /projects with the assignee pre-filled, where
              the pre-fill is visible and dismissible. Nothing here writes an
              assignment. Only for a person with an address — the assignee
              column stores strings, and a person without one has nothing a
              task can be assigned to. */}
          {person.email ? (
            <Link href={`/projects?assignee=${encodeURIComponent(person.email)}`}>
              <Button variant="secondary" size="sm" icon="FolderKanban">
                Assign work
              </Button>
            </Link>
          ) : null}
          {/* Absent without `admin:members:manage`, never disabled (§3.2). */}
          {person.can_manage && onEdit ? (
            <Button
              variant="secondary"
              size="sm"
              icon="Pencil"
              onClick={() => onEdit(person)}
            >
              Edit
            </Button>
          ) : null}
          {/* No close control on the PAGE: the browser's back button is the
              close control for a surface with its own URL, and a second one
              that navigates somewhere of its own choosing is a surprise. */}
          {asPage ? null : (
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label="Close person"
              onClick={onClose}
            />
          )}
        </div>
      </header>

      {/* 1 — Identity. The login badge is the visible half of the two-store
          split: it stops "why can't they see the board" being a mystery. */}
      <section className="space-y-1 border-b border-border p-3 text-xs">
        <Row label="Email">
          {person.email ? (
            <span className="flex flex-wrap items-center gap-1">
              <span className="text-foreground">{person.email}</span>
              <span className={`rounded px-1 py-0.5 ${TONE[person.has_login ? "active" : "warn"]}`}>
                {person.has_login ? "has login" : "directory-only"}
              </span>
            </span>
          ) : (
            <span className="text-muted-foreground">none</span>
          )}
        </Row>
        {person.email_conflict ? (
          <p className="rounded-md border border-border p-2 text-xs text-foreground">
            Another record already claimed <strong>{person.email_conflict}</strong>. Somebody
            has to decide which row is the real person.
          </p>
        ) : null}
        <Row label="Department">{person.department || "—"}</Row>
        <Row label="Team">{person.team || "—"}</Row>
        <Row label="Manager">{person.manager || "—"}</Row>
        <Row label="Status">
          <span className={`rounded px-1 py-0.5 ${TONE[statusTone(person.status)]}`}>
            {person.status}
          </span>
        </Row>
      </section>

      {/* 2 — Skills, each carrying its provenance. */}
      <section className="border-b border-border p-3">
        <h3 className="mb-1 text-xs font-medium text-foreground">Skills</h3>
        {!person.hr_visible ? (
          <p className="text-xs text-muted-foreground">
            Restricted — skills, résumé and capacity need <code>admin:members:read</code>.
          </p>
        ) : (person.skills ?? []).length === 0 ? (
          <p className="text-xs text-muted-foreground">Nobody has added any yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {(person.skills ?? []).map((skill) => {
              const origin = skillOrigin(skill, person.skills_source);
              // WS-28h: the structured row, when one exists, enriches the
              // chip — the level and recency ARE the assignment questions.
              const detail = (person.skills_detail ?? []).find(
                (r) => r.skill === skill
              );
              const assessment = detail ? describeSkill(detail) : "";
              return (
                <span
                  key={skill}
                  title={[
                    origin === "stated"
                      ? "Stated by a person"
                      : "Extracted from a résumé",
                    assessment,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  className={`rounded-md px-2 py-1 text-xs ${
                    origin === "stated"
                      ? "bg-muted text-foreground"
                      : "border border-border text-muted-foreground"
                  }`}
                >
                  {skill}
                  {assessment && (
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      {assessment}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        )}
        {person.hr_visible && person.resume_summary ? (
          <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">
            {person.resume_summary}
          </p>
        ) : null}
      </section>

      {/* 3 — Capacity, as ONE bar computed from open assigned tasks. */}
      <section className="border-b border-border p-3">
        <h3 className="mb-1 text-xs font-medium text-foreground">Capacity</h3>
        {!person.hr_visible ? (
          <p className="text-xs text-muted-foreground">Restricted.</p>
        ) : (
          <>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              {bar.unknown ? null : (
                <div
                  className="h-full bg-primary"
                  style={{ width: `${bar.percent}%` }}
                />
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{bar.label}</p>
          </>
        )}
      </section>

      {/* 4 — Work, scoped by the VIEWER's grants, not the subject's. */}
      <section className="p-3">
        <h3 className="mb-1 text-xs font-medium text-foreground">Open work</h3>
        {!work ? null : !work.available ? (
          <p className="text-xs text-muted-foreground">
            Needs the Projects app to show what they are working on.
          </p>
        ) : work.rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Nothing open that you can see.
          </p>
        ) : (
          <ul className="space-y-1">
            {work.rows.map((task) => (
              <li key={task.id} className="text-xs">
                <a
                  href={`/projects?task=${task.id}`}
                  className="text-foreground hover:underline"
                >
                  {task.task_number ? `#${task.task_number} ` : ""}
                  {task.title}
                </a>
                <span className="text-muted-foreground">
                  {" — "}
                  {task.project_name ?? "a project"} · {task.status_name}
                  {task.due_at ? ` · due ${new Date(task.due_at).toLocaleDateString()}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/*
        5 & 6 — the profile and the employment record.

        `includePrivate` is left on: the server has ALREADY decided, and on a
        colleague's page without `admin:members:manage` those fields came back
        null. Hiding the panel here as well would mean the UI making the access
        decision a second time — the drift D-PC-4 exists to prevent — and a
        person looking at their own page through this panel would lose it.
      */}
      {person.hr_visible && (
        <section className="p-3">
          <AbsencePanel
            target={person.is_self ? "me" : person.id}
            absences={person.absences ?? []}
            hoursThisWeek={person.hours_available_this_week}
            canEdit={(person.editable_fields ?? []).includes("working_hours")}
            onChanged={() => setPerson(null)}
          />
        </section>
      )}

      <section className="p-3">
        <ProfilePanels
          person={person}
          onSaved={(saved) => setPerson(saved)}
        />
      </section>
    </Frame>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <p className="flex gap-2">
      <span className="w-20 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 text-foreground">{children}</span>
    </p>
  );
}
