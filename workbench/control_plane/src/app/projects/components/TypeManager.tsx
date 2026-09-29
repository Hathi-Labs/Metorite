"use client";

/**
 * Task types — the kinds of work a task can be (WS-42 PS-2).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 row PS-2.
 *
 * Until this screen, types could be read and never managed: the routes
 * (`admin.py` `/nodes/{id}/types`, `/types/{id}`) existed with no UI. This is
 * the same shape as `TagManager`: a list with colour and rename, a delete that
 * asks first, and an add form. It draws through `ManagerFrame`, so it is a
 * section of Projects settings and could be a dialog too.
 *
 * Two kinds of row are not this screen's to change. **Epic** is the system
 * type the hierarchy rule keys off (§3.4): the server refuses its rename and
 * its delete, so the controls say so instead of failing. An **organization**
 * row belongs to every space; PS-3 manages those.
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";

import { type TaskTypeRow, projectsApi } from "../lib/api";
import { TAG_COLORS, chipClass } from "../lib/tags";
import { iconOptionsFor, iconValueFor, sortTypes, typeDeleteBody, typeOrgWide } from "../lib/taskTypes";
import ManagerFrame from "./ManagerFrame";

interface Props {
  projectId: string;
  projectName: string;
  onClose: () => void;
  /** The types after a load or a change, so the board's cards follow. */
  onChanged: (types: TaskTypeRow[]) => void;
  /** Drawn as a section of Projects settings, not a dialog (WS-42). */
  inline?: boolean;
}

const ORG_NOTE = "Shared by every space in the organization. Change it under Shared vocabulary.";
const EPIC_NOTE = "Epic is built in: it is the top level, so it cannot be renamed or deleted.";

export function TypeManager({ projectId, projectName, onClose, onChanged, inline = false }: Props) {
  const [types, setTypes] = useState<TaskTypeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState<TaskTypeRow | null>(null);

  const load = async () => {
    try {
      const res = await projectsApi.types(projectId);
      setTypes(res.rows);
      onChanged(res.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let live = true;
    projectsApi
      .types(projectId)
      .then((res) => {
        if (!live) return;
        setTypes(res.rows);
        onChanged(res.rows);
      })
      .catch((err: Error) => live && setError(String(err.message)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // `onChanged` is the caller's; a new function each render must not reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const run = async (work: () => Promise<string | null>) => {
    setError(null);
    setNotice(null);
    try {
      const said = await work();
      if (said) setNotice(said);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  };

  const locked = (t: TaskTypeRow) => typeOrgWide(t) || Boolean(t.is_system);

  return (
    <ManagerFrame
      inline={inline}
      onClose={onClose}
      title="Task types"
      description={`The kinds of work in ${projectName}`}
      icon="Shapes"
      size="lg"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">{error}</p>
      ) : null}
      {notice ? (
        <p className="border-b border-border px-3 py-2 text-xs text-muted-foreground">{notice}</p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : types.length === 0 ? (
          <p className="text-xs text-muted-foreground">No task types yet. Add the first one below.</p>
        ) : (
          <ul className="space-y-1">
            {sortTypes(types).map((t) => (
              <li
                key={t.id}
                className="flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1.5"
              >
                {editing === t.id ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const next = draft.trim();
                      if (!next || next === t.name) {
                        setEditing(null);
                        return;
                      }
                      void run(async () => {
                        const done = await projectsApi.patchType(t.id, { name: next });
                        setEditing(null);
                        return `Renamed to “${done.name}”.`;
                      });
                    }}
                  >
                    <Input
                      autoFocus
                      inputSize="sm"
                      value={draft}
                      aria-label={`Rename ${t.name}`}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key !== "Escape") return;
                        // The first Escape leaves the field, not the dialog.
                        e.stopPropagation();
                        setEditing(null);
                      }}
                    />
                    <Button type="submit" size="sm">
                      Save
                    </Button>
                  </form>
                ) : (
                  <>
                    <span
                      className={`flex min-w-0 items-center gap-1 truncate rounded-md px-1.5 py-0.5 text-[11px] ${chipClass(t.color ?? undefined)}`}
                    >
                      <Icon name={t.icon || "Circle"} className="h-3 w-3 shrink-0" />
                      {t.name}
                    </span>
                    {t.is_system ? <Badge title={EPIC_NOTE}>Top level</Badge> : null}
                    {typeOrgWide(t) ? (
                      <Badge tone="primary" title={ORG_NOTE}>
                        Organization
                      </Badge>
                    ) : null}
                    <span className="flex-1" />
                    <SelectButton
                      label={`Icon for ${t.name}`}
                      widthClass="w-[7.5rem]"
                      value={iconValueFor(t.icon)}
                      disabled={typeOrgWide(t)}
                      onChange={(next) =>
                        void run(async () => {
                          await projectsApi.patchType(t.id, { icon: next });
                          return null;
                        })
                      }
                      options={iconOptionsFor(t.icon).map((c) => ({ value: c, label: c }))}
                    />
                    <SelectButton
                      label={`Colour for ${t.name}`}
                      widthClass="w-[7rem]"
                      value={TAG_COLORS.includes(t.color as never) ? (t.color as string) : "gray"}
                      disabled={typeOrgWide(t)}
                      onChange={(next) =>
                        void run(async () => {
                          await projectsApi.patchType(t.id, { color: next });
                          return null;
                        })
                      }
                      options={TAG_COLORS.map((c) => ({ value: c, label: c }))}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Pencil"
                      aria-label={`Rename ${t.name}`}
                      title={t.is_system ? EPIC_NOTE : typeOrgWide(t) ? ORG_NOTE : undefined}
                      disabled={locked(t)}
                      onClick={() => {
                        setEditing(t.id);
                        setDraft(t.name);
                      }}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Trash2"
                      aria-label={`Delete ${t.name}`}
                      title={
                        t.is_system
                          ? EPIC_NOTE
                          : typeOrgWide(t)
                            ? ORG_NOTE
                            : "Deletes it; its tasks stay, with no type"
                      }
                      disabled={locked(t)}
                      onClick={() => setDeleting(t)}
                    />
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          const next = name.trim();
          if (!next) return;
          void run(async () => {
            await projectsApi.createType(projectId, { name: next, icon: "Circle", color: "gray" });
            setName("");
            return null;
          });
        }}
      >
        <Input
          inputSize="sm"
          value={name}
          aria-label="New task type"
          placeholder="New task type, like Bug or Feature"
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={!name.trim()}>
          Add
        </Button>
      </form>

      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete this task type?"
        subject={deleting?.name ?? null}
        body={typeDeleteBody(deleting?.name ?? "")}
        confirmLabel="Delete"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="Trash2"
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          const target = deleting;
          setDeleting(null);
          if (!target) return;
          void run(async () => {
            const done = await projectsApi.deleteType(target.id);
            const n = done.tasks_untyped;
            return `Deleted “${target.name}”.${n ? ` ${n} task${n === 1 ? "" : "s"} now have no type.` : ""}`;
          });
        }}
      />
    </ManagerFrame>
  );
}
