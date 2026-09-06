"use client";

/**
 * Projects · the status editor — custom lanes, mapped to the four stages that
 * roll up (owner directive 2026-09-03).
 *
 * ## Why this screen did not exist until now
 *
 * `admin.py` has shipped the whole CRUD since migration 146 — create, rename,
 * recolour, re-categorise, reposition, retire, with a 409 that names how many
 * tasks block a delete. `projects/lib/api.ts` carried only the READ. So the
 * status vocabulary was writeable exclusively from SQL, and
 * `project_management_app.md` §9.12.3 spent a paragraph describing what "the
 * status manager already permits" — a manager with a server and no surface.
 *
 * ## The one idea this screen has to teach
 *
 * A status has two halves, and only one of them is the member's.
 *
 * `name` and `color` are theirs: "IN PROCESS", "On the bench", "Waiting on
 * legal". `category` is the machine-readable half, and it is what every other
 * surface keys off — completion stamps `completed_at` when a task crosses into
 * `done` or `cancelled`, the roll-up counts `by_category`, and **Tasks shows
 * the category instead of the name**, because a personal list spanning six
 * projects cannot group by six spellings of "doing it now".
 *
 * So the groups are not decoration and they are not a filter. Filing a lane
 * under a category IS the mapping, and it is the reason the hint text under
 * each group heading spells out the consequence: picking `done` for a lane
 * called "Handed off" silently completes every task that reaches it. That is a
 * large behaviour to leave to a word in a dropdown.
 *
 * ## What it deliberately does NOT offer
 *
 * **No "inherit from space" switch.** Statuses are ROOT-scoped: one set per
 * space, inherited by every project and subproject under it, and a move
 * re-stamps `root_project_id` across the subtree precisely to keep that true
 * (§9.12.3). ClickUp offers a per-list override; we do not, because the
 * category is the only vocabulary two spaces share and every cross-project
 * number rests on it. Owner decision 2026-09-03. The dialog says so in one
 * line rather than showing a radio that would be a lie.
 *
 * **No Triage group.** Owner decision, same date — backlog covers it. The
 * category stays in the vocabulary (`statusCategory.ts` explains why) so an
 * intake-rail row still renders, and `groupByCategory` appends it as a
 * non-editable group rather than hiding lanes the board still draws.
 *
 * **No templates.** ClickUp's "Status template" / "Save as template" needs a
 * store this product does not have. Left out rather than stubbed.
 */

import { ContextMenu } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import {
  ACCENT_HUES,
  type AccentHue,
  accentForHue,
  statusAccent,
} from "@/lib/statusAccent";
import {
  CATEGORY_LABEL,
  EDITABLE_CATEGORIES,
  closesTask,
  groupByCategory,
} from "@/lib/statusCategory";
import { useEffect, useMemo, useState } from "react";

import type { StatusRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import { StatusSetControl } from "./StatusSetControl";
import { accentForStatus } from "../lib/accent";
import {
  emptyCategories,
  placeNew,
  reorder,
} from "../lib/statusOrder";

interface Props {
  /** Any node in the tree — the server resolves the root that owns the set. */
  projectId: string;
  projectName: string;
  onClose: () => void;
  /** Kept in sync while open, so the board relabels without a close. */
  onChanged: (statuses: StatusRow[]) => void;
  /**
   * A rename or a re-categorise changes what every card and lane reads, and a
   * category change can complete or re-open tasks. The board is stale until it
   * refetches.
   */
  onTasksTouched: () => void;
}

export function StatusManager({
  projectId,
  projectName,
  onClose,
  onChanged,
  onTasksTouched,
}: Props) {
  const [rows, setRows] = useState<StatusRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** Which group has its add-row open, and what is typed into it. */
  const [adding, setAdding] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftHue, setDraftHue] = useState<AccentHue>("gray");

  /** The row being renamed, and the text so far. */
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  /** Which row has its palette open. One at a time — a grid of six swatches on
   *  every row would out-shout the names, which are what this screen is for. */
  const [painting, setPainting] = useState<string | null>(null);

  /** Tasks per lane, keyed by status id. Arrives with the lanes. */
  const [counts, setCounts] = useState<Record<string, number>>({});

  /** The row whose overflow menu is open, and where to draw it. */
  const [rowMenu, setRowMenu] = useState<
    { id: string; x: number; y: number } | null
  >(null);

  /**
   * The lane being removed, and where its tasks go.
   *
   * A lane holding tasks cannot simply be deleted, and the old screen said so
   * through a 409 AFTER the click. The row now knows its own count, so the
   * question is asked before it: "move its 6 tasks to…".
   */
  const [removing, setRemoving] = useState<StatusRow | null>(null);
  const [moveTo, setMoveTo] = useState("");

  const load = async () => {
    try {
      const res = await projectsApi.statuses(projectId);
      setRows(res.rows);
      setCounts(res.counts ?? {});
      onChanged(res.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const groups = useMemo(() => groupByCategory(rows), [rows]);
  const gaps = useMemo(() => emptyCategories(rows), [rows]);

  /** Every write goes through here, so one place owns the busy flag, the error
   *  strip and the reload. A half-applied reorder that left the dialog showing
   *  stale positions was the failure worth designing out. */
  async function run(
    work: () => Promise<string | null>,
    { touchesTasks = false }: { touchesTasks?: boolean } = {}
  ) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const said = await work();
      if (said) setNotice(said);
      await load();
      if (touchesTasks) onTasksTouched();
    } catch (err) {
      // The gateway's own sentence, verbatim. Its 409 names how many tasks are
      // in the way, and that number is what tells an owner whether to move
      // three tasks or reconsider the whole lane — "Could not delete" throws
      // away the only useful part.
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  function addStatus(category: string) {
    const name = draftName.trim();
    if (!name) return;
    // Explicit, because `create_status` defaults position to 0 and the seeded
    // rows start at 10 — a status created without one lands at the HEAD of the
    // board. `renumber` is usually empty; it is not when the target category
    // has no gap left, and skipping it writes a position another lane already
    // holds. A tie is broken by NAME, so the board's column order would then
    // depend on spelling. `statusOrder.test.ts` fences both halves.
    const spot = placeNew(rows, category);
    void run(async () => {
      // Before the create, and sequentially: the new row's position is only
      // free once these have moved.
      for (const patch of spot.renumber) {
        await projectsApi.patchStatus(patch.id, { position: patch.position });
      }
      await projectsApi.createStatus(projectId, {
        name,
        category,
        color: draftHue,
        position: spot.position,
      });
      setAdding(null);
      setDraftName("");
      setDraftHue("gray");
      return `Added “${name}” to ${CATEGORY_LABEL[category] ?? category}.`;
    });
  }

  function rename(status: StatusRow) {
    const name = editName.trim();
    setEditing(null);
    if (!name || name === status.name) return;
    void run(
      async () => {
        await projectsApi.patchStatus(status.id, { name });
        return `Renamed to “${name}”.`;
      },
      { touchesTasks: true }
    );
  }

  function recolour(status: StatusRow, color: AccentHue) {
    setPainting(null);
    if (color === status.color) return;
    void run(async () => {
      await projectsApi.patchStatus(status.id, { color });
      return null;
    });
  }

  function recategorise(status: StatusRow, category: string) {
    if (category === status.category) return;
    // Moving stage moves the lane on the board too, or the column order stops
    // matching the lifecycle it is grouped by. Placed against the board WITHOUT
    // this row, since it is leaving where it currently sits — and through
    // `placeNew` for the same no-tie reason the create path uses it.
    const spot = placeNew(
      rows.filter((r) => r.id !== status.id),
      category
    );
    void run(
      async () => {
        for (const patch of spot.renumber) {
          await projectsApi.patchStatus(patch.id, { position: patch.position });
        }
        await projectsApi.patchStatus(status.id, {
          category,
          position: spot.position,
        });
        const crossing =
          closesTask(category) !== closesTask(status.category)
            ? closesTask(category)
              ? " Tasks in it now count as closed."
              : " Tasks in it are open again."
            : "";
        return `“${status.name}” now reports as ${
          CATEGORY_LABEL[category] ?? category
        }.${crossing}`;
      },
      // A category change can stamp or clear `completed_at` on every task in
      // the lane, so this is the one edit that genuinely rewrites task state.
      { touchesTasks: true }
    );
  }

  /**
   * ⚠️ `makeDefault` used to sit here, and it is gone (owner directive
   * 2026-09-06).
   *
   * A status has no default any more. The FIRST lane in a group is where work
   * starts, so the order already on screen IS the answer and moving a lane to
   * the top is how it changes. The flag was a second answer to the same
   * question, and the one nobody could see: on the dev database it sat on
   * `backlog` for every space, which left three of the four category columns
   * with no answer at all. Two answers, one of them usually wrong.
   */
  function move(status: StatusRow, direction: "up" | "down") {
    const patches = reorder(rows, status.id, direction);
    if (patches.length === 0) return;
    void run(async () => {
      // Sequential on purpose. These are two writes to one ordering, and
      // running them in parallel lets the second land first — which is how a
      // swap ends up with both rows on the same position.
      for (const patch of patches) {
        await projectsApi.patchStatus(patch.id, { position: patch.position });
      }
      return null;
    });
  }

  /**
   * Remove a lane, moving whatever is in it somewhere first.
   *
   * `target` is required exactly when the lane holds tasks — the row's own
   * count decides, so the question is asked before the click rather than
   * reported by a 409 after it. The server enforces the same rule and the two
   * refusals it will not take at all (the last lane, and the last CLOSING
   * lane) come back as its own sentence.
   */
  function remove(status: StatusRow, target?: string) {
    void run(
      async () => {
        const gone = await projectsApi.deleteStatus(status.id, target);
        setRemoving(null);
        setMoveTo("");
        return gone.tasks_affected
          ? `Removed “${status.name}” and moved ${gone.tasks_affected} task(s).`
          : `Removed “${status.name}”.`;
      },
      { touchesTasks: Boolean(target) }
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Statuses"
      // The scope is no longer a constant, so the header stops asserting
      // one. `StatusSetControl` names the owner, and it is the thing that
      // knows — a subtitle claiming "shared by everything under it" is
      // wrong the moment a subproject overrides.
      description={projectName}
      icon="Columns3"
      size="lg"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          {notice}
        </p>
      ) : null}

      {/* Where these lanes come from. It replaced three sentences of prose,
          and says the same thing as a control you can act on. */}
      <StatusSetControl
        projectId={projectId}
        busy={busy}
        onError={setError}
        onSwitched={(said) => {
          setNotice(said);
          setError(null);
          void load();
          // The switch moved tasks between lanes and may have completed some,
          // so every board behind this dialog is stale.
          onTasksTouched();
        }}
      />

      {/* Where a lane's tasks go, asked BEFORE the lane is removed.

          The old screen let you click the bin and answered with a 409 naming
          a count. That told you the click was wrong without helping you make
          it right. */}
      {removing ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted px-3 py-2 text-xs">
          <span className="text-foreground">
            Move {counts[removing.id] ?? 0} task(s) out of “{removing.name}” to
          </span>
          <div className="w-40">
            <Select
              inputSize="sm"
              aria-label="Where its tasks go"
              value={moveTo}
              onChange={(e) => setMoveTo(e.target.value)}
            >
              <option value="">Choose a status…</option>
              {rows
                .filter((r) => r.id !== removing.id)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                    {closesTask(r.category) ? " — completes them" : ""}
                  </option>
                ))}
            </Select>
          </div>
          <Button
            variant="primary"
            size="sm"
            disabled={busy || !moveTo}
            onClick={() => remove(removing, moveTo)}
          >
            Move and remove
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setRemoving(null);
              setMoveTo("");
            }}
          >
            Cancel
          </Button>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => (
              <section key={group.category}>
                <header className="flex items-center gap-2">
                  {/* The CATEGORY's hue, with no stored colour in the way —
                      this dot means the stage, not any one lane. `accentForStatus`
                      is for a row; this asks the shared vocabulary directly. */}
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${
                      statusAccent({ category: group.category }).dot
                    }`}
                  />
                  <h3
                    className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
                    title={group.hint}
                  >
                    {group.label}
                  </h3>
                  {/* The explanation, on ask. It used to be a paragraph under
                      every heading — five of them, read once and then read
                      past forever, on a screen whose job is to show a list. */}
                  <Icon
                    name="Info"
                    className="h-3 w-3 shrink-0 text-muted-foreground/60"
                    aria-label={group.hint}
                  />
                  {group.rows.length > 0 ? (
                    <span className="text-[11px] text-muted-foreground">
                      {group.rows.length}
                    </span>
                  ) : null}
                  {group.editable ? (
                    <Button
                      variant="text"
                      size="none"
                      className="ml-auto px-1 py-0.5 text-[11px]"
                      disabled={busy}
                      onClick={() => {
                        setAdding(group.category);
                        setDraftName("");
                        setDraftHue("gray");
                      }}
                    >
                      <Icon name="Plus" className="h-3 w-3" /> Add status
                    </Button>
                  ) : (
                    /* A group the editor shows but does not offer. Saying why
                       beats an inexplicably missing button. */
                    <span className="ml-auto text-[11px] text-muted-foreground">
                      not offered for new lanes
                    </span>
                  )}
                </header>

                <ul className="space-y-1">
                  {group.rows.map((status, index) => {
                    const accent = accentForStatus(status);
                    return (
                      <li
                        key={status.id}
                        className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5"
                      >
                        {/* The colour. A NAME from the six-hue vocabulary,
                            never a hex value — DESIGN_SYSTEM rule 1, and the
                            only way the choice survives light mode and a
                            re-theme. */}
                        <div className="relative shrink-0">
                          <button
                            type="button"
                            aria-label={`Colour of ${status.name}`}
                            aria-expanded={painting === status.id}
                            disabled={busy}
                            onClick={() =>
                              setPainting((c) =>
                                c === status.id ? null : status.id
                              )
                            }
                            className={`h-4 w-4 rounded-full ring-offset-1 ring-offset-background hover:ring-2 hover:ring-ring ${accent.dot}`}
                          />
                          {painting === status.id ? (
                            <div className="absolute left-0 top-6 z-20 flex gap-1 rounded-lg border border-border bg-card p-1.5 shadow-lg">
                              {ACCENT_HUES.map((hue) => (
                                <button
                                  key={hue}
                                  type="button"
                                  aria-label={hue}
                                  aria-pressed={status.color === hue}
                                  onClick={() => recolour(status, hue)}
                                  className={`h-4 w-4 rounded-full ${
                                    accentForHue(hue).dot
                                  } ${
                                    status.color === hue
                                      ? "ring-2 ring-ring ring-offset-1 ring-offset-card"
                                      : "hover:ring-2 hover:ring-ring/50"
                                  }`}
                                />
                              ))}
                            </div>
                          ) : null}
                        </div>

                        {/* The name. Click to rename in place — a rename
                            rewrites what every card reads, so it commits on
                            blur or Enter and abandons on Escape. */}
                        {editing === status.id ? (
                          <Input
                            autoFocus
                            inputSize="sm"
                            className="min-w-0 flex-1 basis-32"
                            aria-label={`Rename ${status.name}`}
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            onBlur={() => rename(status)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") rename(status);
                              if (e.key === "Escape") setEditing(null);
                            }}
                          />
                        ) : (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setEditing(status.id);
                              setEditName(status.name);
                            }}
                            // `basis-32` gives the name a FLOOR. `flex-1
                            // min-w-0` alone let it shrink to 8px, because
                            // everything beside it is `shrink-0` and flexbox
                            // takes the space from whatever is allowed to give
                            // it. The name is the row's subject; it shrinks
                            // last, not first.
                            className="min-w-0 flex-1 basis-32 rounded px-1 py-0.5 text-left text-sm text-foreground hover:bg-muted"
                          >
                            <span className="block truncate">{status.name}</span>
                          </button>
                        )}

                        {/* ⚠️ A chip preview of the name used to sit here, and
                            it was removed after looking at it: the row then
                            printed the same word THREE times — as the editable
                            name, as the chip, and as the selected stage. That
                            is the duplication this session flagged on the list
                            and table views, reproduced by hand. The swatch
                            already carries the colour, and the name is a
                            keystroke away. */}

                        {/* How many tasks are in this lane.

                            Not decoration: it is what turns "remove this
                            lane" from a click that returns a 409 into a
                            question the screen can ask first. */}
                        <span
                          className="shrink-0 tabular-nums text-[11px] text-muted-foreground"
                          title={`${counts[status.id] ?? 0} task(s) in this lane`}
                        >
                          {counts[status.id] ?? 0}
                        </span>

                        {/* Everything else this row can do, behind ONE control.

                            ⚠️ The row used to carry five: a Default chip or a
                            "Set default" button, a stage dropdown, two arrows
                            and a bin. The dropdown was the worst of them — it
                            named the stage the row was ALREADY filed under by
                            sitting inside that group's heading, so every row
                            stated its category twice and the name, which is the
                            thing this screen exists to edit, was squeezed to
                            fit. The group IS the stage. */}
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          icon="MoreHorizontal"
                          aria-label={`Actions for ${status.name}`}
                          disabled={busy}
                          onClick={(e) => {
                            const box =
                              e.currentTarget.getBoundingClientRect();
                            setRowMenu({
                              id: status.id,
                              x: box.left,
                              y: box.bottom + 2,
                            });
                          }}
                        />
                        {rowMenu?.id === status.id ? (
                          <ContextMenu
                            x={rowMenu.x}
                            y={rowMenu.y}
                            onClose={() => setRowMenu(null)}
                            items={[
                              {
                                kind: "item",
                                label: "Rename",
                                icon: themedIcon("PenLine"),
                                onSelect: () => {
                                  setEditing(status.id);
                                  setEditName(status.name);
                                },
                              },
                              ...(index > 0
                                ? [
                                    {
                                      kind: "item" as const,
                                      label: "Move up",
                                      icon: themedIcon("ChevronUp"),
                                      onSelect: () => move(status, "up"),
                                    },
                                  ]
                                : []),
                              ...(index < group.rows.length - 1
                                ? [
                                    {
                                      kind: "item" as const,
                                      label: "Move down",
                                      icon: themedIcon("ChevronDown"),
                                      onSelect: () => move(status, "down"),
                                    },
                                  ]
                                : []),
                              { kind: "sep" },
                              { kind: "label", label: "Move to stage" },
                              ...EDITABLE_CATEGORIES.map((category) => ({
                                kind: "item" as const,
                                label: CATEGORY_LABEL[category] ?? category,
                                checked: category === status.category,
                                onSelect: () =>
                                  recategorise(status, category),
                              })),
                              { kind: "sep" },
                              {
                                kind: "item",
                                label: "Remove",
                                icon: themedIcon("Trash2"),
                                danger: true,
                                onSelect: () => {
                                  // With tasks in it the lane needs a
                                  // destination, so the row opens the
                                  // question instead of guessing.
                                  if (counts[status.id]) {
                                    setRemoving(status);
                                    setMoveTo("");
                                    return;
                                  }
                                  remove(status);
                                },
                              },
                            ]}
                          />
                        ) : null}
                      </li>
                    );
                  })}

                  {adding === group.category ? (
                    <li className="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1.5">
                      <div className="flex shrink-0 gap-1">
                        {ACCENT_HUES.map((hue) => (
                          <button
                            key={hue}
                            type="button"
                            aria-label={hue}
                            aria-pressed={draftHue === hue}
                            onClick={() => setDraftHue(hue)}
                            className={`h-4 w-4 rounded-full ${
                              accentForHue(hue).dot
                            } ${
                              draftHue === hue
                                ? "ring-2 ring-ring ring-offset-1 ring-offset-background"
                                : "hover:ring-2 hover:ring-ring/50"
                            }`}
                          />
                        ))}
                      </div>
                      <Input
                        autoFocus
                        inputSize="sm"
                        className="min-w-0 flex-1 basis-32"
                        placeholder={`Name a ${group.label.toLowerCase()} lane`}
                        aria-label={`New ${group.label} status`}
                        value={draftName}
                        onChange={(e) => setDraftName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") addStatus(group.category);
                          if (e.key === "Escape") setAdding(null);
                        }}
                      />
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={busy || !draftName.trim()}
                        onClick={() => addStatus(group.category)}
                      >
                        Add
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setAdding(null)}
                      >
                        Cancel
                      </Button>
                    </li>
                  ) : null}

                  {group.rows.length === 0 && adding !== group.category ? (
                    <li className="rounded-md border border-dashed border-border px-2 py-1.5 text-[11px] text-muted-foreground">
                      No {group.label.toLowerCase()} lane.
                      {group.category === "done"
                        ? " Nothing in this space can be completed until there is one."
                        : ""}
                    </li>
                  ) : null}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>

      {/* Two facts a member needs and cannot see anywhere else: the scope, and
          which stages are unrepresented. */}
      <div className="border-t border-border px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        {gaps.length > 0 ? (
          <p className="mt-1 text-foreground">
            No lane reports as{" "}
            {gaps.map((c) => CATEGORY_LABEL[c] ?? c).join(", ")}.
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

export default StatusManager;
