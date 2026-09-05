/**
 * The status colour vocabulary — one palette for /projects and /tasks (WS-27ad).
 *
 * Before this module there were three vocabularies and a fact nobody drew:
 *
 *  1. `app/tasks/lib/stageColors.ts` — a real accent system (dot / soft / text /
 *     bar) resolved from the stage NAME, because /tasks' stages are user-typed
 *     and nothing machine-readable exists to key off. Its board columns, list
 *     group headers and status pill all read the same because of it.
 *  2. `app/projects/lib/tags.ts` — a six-name chip palette used for tags only.
 *  3. `pm_task_statuses.color` — stored since migration 146, exposed on the API
 *     as `StatusRow.color`, and **rendered nowhere**: every Projects board
 *     column drew the same `bg-muted`, so a Done lane and a Backlog lane looked
 *     identical while the /tasks board next door was colour-coded.
 *
 * So this is a merge, not a redesign. Both existing palettes were already
 * token-only (`DESIGN_SYSTEM.md` forbids a literal), and the class strings below
 * are theirs — which is what lets /tasks keep rendering byte-identically while
 * gaining a shared home.
 *
 * ## Precedence
 *
 * An accent is resolved by asking, in order, for the most authoritative fact
 * available. Each step exists because one of the two apps has that fact and the
 * other does not:
 *
 *   1. **stored colour** — `status.color`, `tag.color`. Somebody chose it; a
 *      derived hue must never overrule a human.
 *   2. **category** — Projects' six machine-readable values
 *      (`routes/projects/core.py` `STATUS_CATEGORIES`). Projects *knows* what a
 *      lane means; guessing from its name would be worse information.
 *   3. **name keyword** — /tasks' stages are user-named and have no category, so
 *      "Done"/"Waiting"/"In progress" are read out of the words. The regexes are
 *      ported unchanged from `stageColors.ts`.
 *   4. **positional** — nothing is known, so give neighbouring lanes different
 *      hues. `lastIsDone` additionally paints the final lane green, which is
 *      /tasks' rule (dropping on the last stage completes the task) and NOT
 *      Projects' (where a category already says so).
 *
 * Unknown input never throws and never returns undefined: an unrecognised colour
 * name or category falls through to the next step, and the end of the chain is
 * always a hue. A board that renders no columns because somebody typed a colour
 * we do not know is a worse failure than a grey column.
 */

/**
 * The accent shape /tasks proved, plus the chip pair tags need.
 *
 * Five slots rather than one string because a hue is used five ways and the
 * variations are not derivable from each other: `soft` is a fill behind text,
 * `text` must stay readable ON that fill, `dot` is a solid marker, `bar` is a
 * left border, and `chip` is a self-contained pill whose gray and violet
 * deliberately differ from `soft`+`text` (see CHIP below).
 */
export interface StatusAccent {
  /** The solid marker: a column's cap, the dot beside a label. */
  dot: string;
  /** A faint tinted background for a header strip or pill. */
  soft: string;
  /** A readable text tone on the `soft` background. */
  text: string;
  /** The left border accent of a list group header. */
  bar: string;
  /** A complete pill: background AND text, for a tag or status chip. */
  chip: string;
}

/**
 * The palette, by name.
 *
 * **`gray`, not `grey`.** Both spellings existed — `stageColors` wrote `grey`
 * internally, the database writes `gray` (`146_projects.sql`, `DEFAULT 'gray'`)
 * and so does `TAG_COLORS`. The stored spelling wins, because that is the one a
 * row can contain; `grey` is accepted as an alias so no call site has to care.
 */
export const ACCENT_HUES = [
  "gray",
  "red",
  "amber",
  "green",
  "blue",
  "violet",
] as const;

export type AccentHue = (typeof ACCENT_HUES)[number];

const ACCENTS: Record<AccentHue, StatusAccent> = {
  gray: {
    dot: "bg-muted-foreground/60",
    soft: "bg-muted/40",
    text: "text-muted-foreground",
    bar: "border-l-muted-foreground/40",
    // Deliberately not `soft`+`text`: a tag chip sits on a card and needs a
    // solid-enough fill to read as a chip, which `bg-muted/40` does not give.
    chip: "bg-secondary text-muted-foreground",
  },
  red: {
    dot: "bg-destructive",
    soft: "bg-destructive/10",
    text: "text-destructive",
    bar: "border-l-destructive",
    chip: "bg-destructive/10 text-destructive",
  },
  amber: {
    dot: "bg-warning",
    soft: "bg-warning/10",
    text: "text-warning",
    bar: "border-l-warning",
    chip: "bg-warning/10 text-warning",
  },
  green: {
    dot: "bg-success",
    soft: "bg-success/10",
    text: "text-success",
    bar: "border-l-success",
    chip: "bg-success/10 text-success",
  },
  blue: {
    // ⚠️ `--info`, NOT `--primary`. `--primary` is the accent a member picks at
    // Settings → Appearance, so while these read it, "In progress" was not
    // blue — it was whatever colour the viewer chose. Set the accent to green
    // and an active lane became the same colour as Done. Measured 2026-09-03.
    //
    // A status says what is TRUE of the work. An accent says what the member
    // likes. One must not move the other, and `statusAccent.test.ts` now fails
    // if a hue in this map reaches for `primary` again.
    dot: "bg-info",
    soft: "bg-info/10",
    text: "text-info",
    bar: "border-l-info",
    chip: "bg-info/10 text-info",
  },
  violet: {
    // ⚠️ This was `--primary` at 60% opacity, which made it BLUE ONE STEP
    // FAINTER rather than a fifth colour. Two of the five lanes on a
    // positionally-coloured board never differed by hue at all. It has its own
    // token now, so `POSITIONAL` really does hand out four distinct hues.
    dot: "bg-violet",
    soft: "bg-violet/10",
    text: "text-violet",
    bar: "border-l-violet",
    // The tag palette's violet, kept as it was: `accent` is the one token pair
    // that gives a distinct chip without competing with `primary`.
    chip: "bg-accent text-accent-foreground",
  },
};

/** Spellings a stored value may legitimately use. */
const COLOR_ALIASES: Record<string, AccentHue> = {
  grey: "gray",
  gray: "gray",
  red: "red",
  amber: "amber",
  orange: "amber",
  yellow: "amber",
  green: "green",
  blue: "blue",
  violet: "violet",
  purple: "violet",
};

/**
 * `pm_task_statuses.category` → hue.
 *
 * **Agrees with `keywordHue` below, lane for lane — that agreement IS the
 * feature.** A category and a name are two ways of learning the same thing, so
 * if they disagreed, the same lane would render one colour in /projects (which
 * has categories) and another in /tasks (which has only names), which is the
 * exact divergence this module exists to end. `test_category_and_keyword_agree`
 * is the fence.
 *
 * It was briefly the other way round: this map was written to match the seeded
 * colours in `routes/projects/tree.py` (`To do` blue, `In progress` amber), and
 * the result was that two of the four default lanes still read differently in
 * the two apps. The seed moved to match this instead — the semantics belong to
 * the token vocabulary, not to whatever four rows a migration happened to
 * insert. `bg-primary` is this UI's "active" tone everywhere else, so
 * `in_progress` is blue; nothing has started in `backlog` or `todo`, so both
 * stay muted.
 *
 * `cancelled` is terminal-and-not-successful, which is what the destructive
 * tone means everywhere else here. `triage` (WS-27u) is the
 * parked-at-the-front-door lane; /tasks has no counterpart, so it takes the one
 * remaining distinct hue freely.
 *
 * ⚠️ `backlog` and `todo` are both gray, so a board carrying both draws two
 * muted lanes side by side. That is /tasks' existing behaviour (its keyword
 * rule maps both words to grey) and matching it is the point of this change,
 * not a regression introduced by it. An owner who wants them distinct now has a
 * working lever for the first time: `status.color` outranks this map, and as of
 * WS-27ad it is actually rendered.
 */
const CATEGORY_HUES: Record<string, AccentHue> = {
  backlog: "gray",
  todo: "gray",
  in_progress: "blue",
  done: "green",
  cancelled: "red",
  triage: "violet",
};

/**
 * Name keywords, ported byte-for-byte from `stageColors.ts`.
 *
 * Unchanged on purpose: /tasks' rendering is driven entirely by these, and
 * "improving" one regex here silently repaints somebody's board. A missing case
 * (there is no `cancel` rule) is a deliberate non-change, not an oversight —
 * adding it would recolour any /tasks stage named "Cancelled".
 */
function keywordHue(name: string): AccentHue | null {
  const n = name.toLowerCase();
  if (/(done|complete|closed|finished|shipped)/.test(n)) return "green";
  if (/(wait|block|hold|paused|stuck)/.test(n)) return "amber";
  if (/(progress|doing|active|working|review|in[\s-]?process)/.test(n))
    return "blue";
  if (/(todo|to[\s-]?do|backlog|new|open|inbox)/.test(n)) return "gray";
  return null;
}

/** Positional fallback hues, in axis order. Keeps early lanes distinct. */
const POSITIONAL: AccentHue[] = ["gray", "blue", "violet", "amber"];

/** Everything either app knows about one lane, tag or status. All optional. */
export interface AccentInput {
  /** A stored colour NAME — `pm_task_statuses.color`, `pm_tags.color`. */
  color?: string | null;
  /** A machine-readable category — Projects' `STATUS_CATEGORIES`. */
  category?: string | null;
  /** The human-facing name, for axes nobody categorised (/tasks' stages). */
  name?: string | null;
  /** Where this lane sits on its axis, for the positional fallback. */
  index?: number;
  /** How many lanes the axis has. */
  total?: number;
  /**
   * Paint the LAST lane green even without a keyword.
   *
   * /tasks' rule: dropping on the final stage marks the task done, so the final
   * stage IS the done stage. Projects must not inherit it — a lane's meaning
   * there comes from its category, never from where it happens to sit.
   */
  lastIsDone?: boolean;
}

/**
 * The hue for one lane. Exported so the precedence itself is testable without
 * asserting on class strings.
 */
export function resolveHue(input: AccentInput): AccentHue {
  const stored = input.color?.trim().toLowerCase();
  if (stored && stored in COLOR_ALIASES) return COLOR_ALIASES[stored];

  const category = input.category?.trim().toLowerCase();
  if (category && category in CATEGORY_HUES) return CATEGORY_HUES[category];

  if (input.name) {
    const keyword = keywordHue(input.name);
    if (keyword) return keyword;
  }

  const index = input.index ?? 0;
  const total = input.total ?? 0;
  if (input.lastIsDone && total > 0 && index === total - 1) return "green";
  return POSITIONAL[Math.abs(index) % POSITIONAL.length];
}

/** The accent for one lane, tag or status. */
export function statusAccent(input: AccentInput): StatusAccent {
  return ACCENTS[resolveHue(input)];
}

/** The accent for a bare hue name — the tag picker's swatch list. */
export function accentForHue(hue: AccentHue): StatusAccent {
  return ACCENTS[hue];
}

/* ── Project run state (WS-27bg / D-PM-27) ─────────────────────────────────
 *
 * A PROJECT's run state is a different axis from a TASK's status, and this is
 * the one module either of them becomes a colour in (rule 4).
 *
 * 🔴 **This map deliberately disagrees with `CATEGORY_HUES` on two hues, and
 * the disagreement is a recorded owner decision (D-PM-27), not a bug.** A task
 * board says `in_progress` in blue and `done` in green; a project tree says
 * *running* in green and *finished* in blue. The owner was shown that cost —
 * the two sit on the same screen — and chose this mapping. **An agent finding
 * it inconsistent should cite D-PM-27 and stop, not repaint it.**
 *
 * 🔴 **It is a CLOSED lookup and must never fall through `resolveHue`.**
 * `keywordHue` maps the literal word `active` → **blue** and `done` → **green**
 * — i.e. routing a run state through the generic resolver yields the exact
 * OPPOSITE of the decision on two of the five states. `projectStateAccent`
 * therefore indexes `ACCENTS` directly. `statusAccent.test.ts` pins the
 * divergence so a future "simplification" into `resolveHue` goes red.
 *
 * **The stored value is not the label.** `active` shows as *Ongoing* and
 * `on_hold` as *Paused* (D-PM-25): R6 forbids renaming a column in place, and
 * `active` is the DEFAULT on every existing row, so the display name lives here
 * — the same split `pm_task_statuses` already has between a free `name` and a
 * machine-readable `category`.
 *
 * **Every state carries a GLYPH as well as a hue.** A dense tree read at a
 * glance, or by somebody who cannot separate green from amber, must not depend
 * on colour alone — so the icon is part of the vocabulary rather than a choice
 * each call site makes.
 */

export interface ProjectStateVisual {
  /** What a human sees. Never the stored value. */
  label: string;
  /** Resolved through this module's own palette — never a class string. */
  hue: AccentHue;
  /** Lucide name for `<Icon name=… />`; the active theme picks the pack. */
  icon: string;
}

export const PROJECT_STATES: Record<string, ProjectStateVisual> = {
  queued: { label: "Queued", hue: "gray", icon: "CircleDashed" },
  active: { label: "Ongoing", hue: "green", icon: "CircleDot" },
  on_hold: { label: "Paused", hue: "amber", icon: "CirclePause" },
  stopped: { label: "Stopped", hue: "red", icon: "CircleStop" },
  done: { label: "Done", hue: "blue", icon: "CircleCheck" },
};

/**
 * The run states in lifecycle order — what a picker offers.
 *
 * ⚠️ Mirrors `routes/projects/core.RUN_STATES`. It deliberately does NOT carry
 * `archived`: that is the other axis (D-PM-25), reached by the archive action,
 * and offering it in a run-state picker is how the two-facts-one-column defect
 * this decision removed would come back through the UI.
 */
export const PROJECT_STATE_ORDER = [
  "queued",
  "active",
  "on_hold",
  "stopped",
  "done",
] as const;

/** How a run state draws, or `null` when the value is not one. */
export function projectState(value?: string | null): ProjectStateVisual | null {
  const key = value?.trim().toLowerCase();
  return (key && PROJECT_STATES[key]) || null;
}

/**
 * The accent for a project run state.
 *
 * Falls back to `gray` rather than throwing: a row carrying a value this client
 * does not know (an older row, a newer server) should draw a neutral dot, not
 * blank the tree — the same "unknown input never throws" rule `resolveHue`
 * follows at the end of its chain.
 */
export function projectStateAccent(value?: string | null): StatusAccent {
  const state = projectState(value);
  return ACCENTS[state ? state.hue : "gray"];
}
