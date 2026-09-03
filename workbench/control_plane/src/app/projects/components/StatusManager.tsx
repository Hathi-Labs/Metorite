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

import Icon from "@/components/Icon";
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
import { accentForStatus } from "../lib/accent";
import {
  emptyCategories,
  isLastInCategory,
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

  const load = async () => {
    try {
      const res = await projectsApi.statuses(projectId);
      setRows(res.rows);
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

  function makeDefault(status: StatusRow) {
    void run(async () => {
      await projectsApi.patchStatus(status.id, { is_default: true });
      return `New work in ${
        CATEGORY_LABEL[status.category] ?? status.category
      } lands in “${status.name}”.`;
    });
  }

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

  function remove(status: StatusRow) {
    void run(async () => {
      const gone = await projectsApi.deleteStatus(status.id);
      void gone;
      return `Removed “${status.name}”.`;
    });
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Statuses"
      description={`Shared by ${projectName} and everything under it`}
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

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {/* The one sentence that explains the whole screen. It is here rather
            than in a tooltip because the grouping looks like a filter until
            somebody tells you it is the mapping. */}
        <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
          Name your lanes whatever this space calls them. The{" "}
          <span className="font-medium text-foreground">stage</span> each lane
          sits under is what rolls up — it drives the space roll-up, the
          overdue counts, and what <span className="font-medium text-foreground">Tasks</span>{" "}
          shows, which is the stage and never the lane name.
        </p>

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
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {group.label}
                  </h3>
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
                <p className="mb-1.5 mt-0.5 pl-4 text-[11px] leading-relaxed text-muted-foreground">
                  {group.hint}
                </p>

                <ul className="space-y-1">
                  {group.rows.map((status, index) => {
                    const accent = accentForStatus(status);
                    const last = isLastInCategory(rows, status.id);
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

                        {status.is_default ? (
                          <span
                            className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                            title={`New ${
                              CATEGORY_LABEL[status.category] ?? status.category
                            } work lands here.`}
                          >
                            Default
                          </span>
                        ) : (
                          <Button
                            variant="text"
                            size="none"
                            className="shrink-0 px-1 py-0.5 text-[10px]"
                            disabled={busy}
                            title={`Make this the default ${
                              CATEGORY_LABEL[status.category] ?? status.category
                            } lane`}
                            onClick={() => makeDefault(status)}
                          >
                            Set default
                          </Button>
                        )}

                        {/* Stage. The mapping control, and the only edit here
                            that can complete or re-open tasks.

                            ⚠️ WRAPPED, and it has to be. `Select` renders
                            `<div className="relative w-full">` around the
                            element and passes `className` to the `<select>`
                            only, so a caller cannot size it: `w-auto shrink-0`
                            landed on the inner element and the wrapper still
                            took `w-full`. Measured: the wrapper claimed 218px
                            of a 486px row and squeezed the NAME button — the
                            one thing this screen exists to edit — down to 8px.
                            The fix belongs in the primitive, and changing a
                            shared control's box model is not this change. */}
                        <div className="w-28 shrink-0">
                        <Select
                          inputSize="sm"
                          aria-label={`Stage of ${status.name}`}
                          value={status.category}
                          disabled={busy}
                          onChange={(e) => recategorise(status, e.target.value)}
                        >
                          {EDITABLE_CATEGORIES.map((category) => (
                            <option key={category} value={category}>
                              {CATEGORY_LABEL[category] ?? category}
                            </option>
                          ))}
                          {/* A stored category the create path does not offer
                              still has to be selectable, or opening this
                              dropdown would silently re-file the lane. */}
                          {(EDITABLE_CATEGORIES as readonly string[]).includes(
                            status.category
                          ) ? null : (
                            <option value={status.category}>
                              {CATEGORY_LABEL[status.category] ?? status.category}
                            </option>
                          )}
                        </Select>
                        </div>

                        <div className="flex shrink-0 items-center">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            icon="ChevronUp"
                            aria-label={`Move ${status.name} up`}
                            disabled={busy || index === 0}
                            onClick={() => move(status, "up")}
                          />
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            icon="ChevronDown"
                            aria-label={`Move ${status.name} down`}
                            disabled={busy || index === group.rows.length - 1}
                            onClick={() => move(status, "down")}
                          />
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            icon="Trash2"
                            aria-label={`Remove ${status.name}`}
                            disabled={busy || last}
                            // Explained BEFORE the click. The server refuses
                            // this too, but a disabled button with no reason is
                            // how somebody concludes the screen is broken.
                            title={
                              last
                                ? `The only ${
                                    CATEGORY_LABEL[status.category] ??
                                    status.category
                                  } lane. Add another before removing this one.`
                                : `Remove ${status.name}`
                            }
                            onClick={() => remove(status)}
                          />
                        </div>
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
        <p>
          One set per space. Every project and subproject under {projectName}{" "}
          shares it, so two spaces stay comparable through the stage.
        </p>
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
