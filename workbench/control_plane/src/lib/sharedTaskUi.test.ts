/**
 * The /projects ↔ /tasks shared seam, made mechanical (WS-27ad, done-when 4).
 *
 * Round 1 of the continuity backport promoted the chip vocabulary, the keyboard
 * cursor, the group-context quick-add and the post-drop flash into `src/lib/`
 * and `src/components/`, leaving app-local re-export shims behind. Round 2 added
 * the colour vocabulary and the card shell. All of it works exactly as long as
 * nobody re-declares their own copy — and a second copy is invisible in review,
 * because both apps keep passing their own tests while slowly diverging. That
 * is how the two boards got two palettes in the first place.
 *
 * So the rule is a test: **the shared module is the only declaration, and both
 * apps reach it.** Re-export shims are fine and expected (they keep app-local
 * import paths working); a shim that grows a body is not.
 *
 * Rooted at `src/` via `import.meta.url`, never at the process cwd — a checkout
 * with agent worktrees under `.claude/worktrees/` would otherwise be scanned as
 * part of itself and report every shared module as duplicated.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry))
        out.push(relative(SRC, full).split(sep).join("/"));
    }
  };
  walk(SRC);
  return out.sort();
}

const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const FILES = sourceFiles();

/**
 * One shared thing: where it is declared, and how a re-declaration would read.
 *
 * The pattern deliberately matches a DECLARATION (`export function foo`,
 * `const FOO: Record<…> =`) rather than a mention, so a file that imports the
 * symbol, re-exports it, or names it in a comment is not an offender.
 */
const SEAM: {
  what: string;
  home: string;
  declaration: RegExp;
  /** Files allowed to match anyway, each with the argument for why (S1). */
  except?: Record<string, string>;
}[] = [
  {
    what: "the keyboard cursor",
    home: "lib/cursor.ts",
    declaration: /export\s+function\s+(stepCursor|clampCursor)\b/,
  },
  {
    what: "the group-context quick-add control",
    home: "components/QuickAdd.tsx",
    declaration: /export\s+function\s+QuickAdd\b/,
  },
  {
    what: "the post-drop flash",
    home: "components/useFlash.ts",
    declaration: /export\s+function\s+useFlash\b/,
  },
  {
    what: "the selection grammar",
    home: "lib/selection.ts",
    declaration: /export\s+function\s+(clickSelect|range|toggle|prune)\b/,
  },
  {
    what: "the drop-gap target",
    home: "components/DropGap.tsx",
    declaration: /export\s+function\s+DropGap\b/,
  },
  {
    what: "the drop-index arithmetic",
    home: "lib/boardDrop.ts",
    declaration: /export\s+function\s+(dropIndexFor|gapKey)\b/,
  },
  {
    what: "the chip vocabulary",
    home: "lib/taskCard.ts",
    declaration: /export\s+type\s+MetaTone\b/,
  },
  {
    what: "the chip renderer",
    home: "components/TaskMeta.tsx",
    declaration: /export\s+function\s+TaskMeta\b/,
  },
  {
    /**
     * S6. `MetaTone` grew a `warning` slot for the Projects priority chip, and
     * the tempting shortcut was to write `text-warning` at the call site — one
     * class, no shared edit, and the descriptor stops being a name. This is the
     * fence: the tone→class table lives in the renderer and nowhere else, so a
     * second app cannot decide that its warning is amber-600.
     */
    what: "the chip tone→class table",
    home: "components/TaskMeta.tsx",
    declaration: /(?:^|\n)\s*const\s+TONE\s*:\s*Record<MetaTone/,
  },
  {
    what: "the status colour vocabulary",
    home: "lib/statusAccent.ts",
    declaration: /export\s+function\s+(statusAccent|resolveHue)\b/,
  },
  {
    what: "the status pill",
    home: "components/StatusChip.tsx",
    declaration: /export\s+function\s+StatusChip\b/,
  },
  {
    what: "the task card shell",
    home: "components/TaskCardShell.tsx",
    declaration: /export\s+function\s+TaskCardShell\b/,
  },
  {
    what: "the card title, and how many lines it gets",
    home: "components/TaskCardShell.tsx",
    declaration: /export\s+function\s+TaskCardTitle\b/,
  },
  {
    /**
     * S1. The offender this catches was NOT exported — `app/tasks/components/
     * TaskCard.tsx` held a private `Avatar` + `AvatarStack` pair drawing the
     * same initials, the same "+N" and the same ring as the shared one. So the
     * `export` is optional in this pattern, unlike every row above it: a second
     * copy is a defect whether or not anybody else can import it.
     */
    what: "the avatar stack",
    home: "components/TaskMeta.tsx",
    declaration: /(?:^|\n)\s*(?:export\s+)?function\s+AvatarStack\b/,
    except: {
      "components/room/Identity.tsx":
        "a name collision, not a second copy: the room strip takes " +
        "RoomParticipant rows, draws photographs with a presence ring, and " +
        "colours each face by the per-person identity hue that " +
        "conformance.test.ts excepts from the colour rule precisely because it " +
        "must NOT follow the theme. Folding it into TaskMeta's initials would " +
        "mean teaching a task chip about presence. Renaming one of the two is " +
        "the real fix and it is a rooms decision, not a Projects↔Tasks one",
    },
  },
  {
    what: "the empty state",
    home: "components/EmptyState.tsx",
    declaration: /export\s+function\s+EmptyState\b/,
  },
  {
    /**
     * WS-27bd. `/projects` had zero `onContextMenu` and a working right-click
     * menu already existed under `app/tasks/components/`, wired at five call
     * sites. The likeliest way to "add a context menu to the board" is to write
     * a third one — so the menu was **promoted** to `components/ContextMenu.tsx`
     * (a re-export shim keeps /tasks' import path) and this row is the fence.
     *
     * The `export` is optional in the pattern, as it is for the avatar stack:
     * the copy this has to see is NOT exported.
     */
    what: "the right-click menu",
    home: "components/ContextMenu.tsx",
    declaration: /(?:^|\n)\s*(?:export\s+)?function\s+ContextMenu\b/,
    except: {
      "app/email/components/EmailList.tsx":
        "a THIRD copy, pre-existing and deliberately left alone. It is not a " +
        "name collision like the room strip's AvatarStack — it is genuinely " +
        "the same interaction — but it carries flyout submenus, bulk-vs-single " +
        "fan-out and a label picker that the flat shared menu does not " +
        "express, so folding it in is an email ticket with its own acceptance, " +
        "not a side effect of a Projects↔Tasks slice (CLAUDE.md §5: existing " +
        "violations are findings for the board). Recorded rather than silently " +
        "passed, so the fence is neither red on arrival nor lying. Delete this " +
        "exemption in the change that retires it.",
    },
  },
  {
    /**
     * The ITEM vocabulary, not only the renderer. A surface that declared its
     * own item union would be free to grow a kind the shared menu cannot draw
     * — which is how the email copy acquired submenus in the first place.
     */
    what: "the right-click menu's item vocabulary",
    home: "components/ContextMenu.tsx",
    declaration: /export\s+type\s+CtxItem\b/,
  },
];

describe("one implementation, consumed twice", () => {
  it.each(SEAM)(
    "$what is declared only in $home",
    ({ home, declaration, except }) => {
      const offenders = FILES.filter(
        (f) => f !== home && !(f in (except ?? {})) && declaration.test(read(f)),
      );
      expect(
        offenders,
        `A second copy of what ${home} owns. Import it (or re-export it); ` +
          "two implementations of one interaction is how /projects and /tasks " +
          "stopped looking like one product.",
      ).toEqual([]);
    },
  );

  it.each(SEAM)("$home exists and still declares it", ({ home, declaration }) => {
    expect(FILES, `${home} is missing — update SEAM in this file`).toContain(home);
    expect(read(home)).toMatch(declaration);
  });

  it.each(SEAM.filter((s) => s.except))(
    "every exemption from $what still names a real one",
    ({ declaration, except }) => {
      // An exemption outliving its offender is latitude nobody asked for — the
      // same rule conformance.test.ts applies to PALETTE_EXCEPTIONS.
      for (const f of Object.keys(except ?? {})) {
        expect(FILES, `${f} is gone — drop its exemption`).toContain(f);
        expect(
          read(f),
          `${f} no longer declares this — drop its exemption`,
        ).toMatch(declaration);
      }
    },
  );
});

describe("both apps reach the shared modules", () => {
  /** Files under an app that import from a shared path, directly or via a shim. */
  const importsFrom = (app: string, module: string) =>
    FILES.filter(
      (f) =>
        f.startsWith(`app/${app}/`) &&
        new RegExp(`from ["']@/${module}["']`).test(read(f)),
    );

  /** A shim: an app-local file whose whole body re-exports the shared one. */
  const shims = (app: string, module: string) =>
    importsFrom(app, module).filter((f) => /export\s*(\{|\*)/.test(read(f)));

  const reaches = (app: string, module: string) =>
    importsFrom(app, module).length > 0;

  it.each([
    ["projects", "lib/cursor"],
    ["projects", "lib/selection"],
    ["projects", "components/QuickAdd"],
    ["projects", "components/useFlash"],
    ["projects", "lib/statusAccent"],
    ["projects", "components/StatusChip"],
    ["projects", "components/TaskCardShell"],
    ["projects", "components/DropGap"],
    ["projects", "lib/boardDrop"],
    // S4 — /tasks is deliberately absent: `ItemList.tsx` still holds the
    // original local `NoMatchState`/`EmptyState` pair this was promoted FROM,
    // and retiring them onto the shared box is a `/tasks` edit that another
    // slice holds open. Add the row in the change that does it.
    ["projects", "components/EmptyState"],
    // S6 — the chip vocabulary itself, not only its renderer. Each app reaches
    // it through its own adapter (`projects/lib/card.ts`,
    // `tasks/lib/cardMeta.ts`); an app that stopped importing it has grown a
    // second rulebook for which chips a task earns.
    ["projects", "lib/taskCard"],
    ["projects", "components/TaskMeta"],
    // WS-27bd — the right-click menu, consumed by BOTH apps. /projects reaches
    // it directly (`TaskBoard`); /tasks through the shim at its old
    // path, which is what keeps its five call sites unedited.
    ["projects", "components/ContextMenu"],
    ["tasks", "components/ContextMenu"],
    ["tasks", "lib/taskCard"],
    ["tasks", "components/TaskMeta"],
    ["tasks", "lib/cursor"],
    ["tasks", "lib/selection"],
    ["tasks", "components/QuickAdd"],
    ["tasks", "components/useFlash"],
    ["tasks", "lib/statusAccent"],
    ["tasks", "components/StatusChip"],
    ["tasks", "components/TaskCardShell"],
    ["tasks", "components/DropGap"],
    ["tasks", "lib/boardDrop"],
  ])("/%s consumes @/%s", (app, module) => {
    expect(
      reaches(app, module),
      `/${app} no longer imports @/${module} — either it grew its own copy ` +
        "(the failure this test exists for) or it genuinely stopped needing " +
        "it, in which case drop the row.",
    ).toBe(true);
  });

  it("the shims stay shims", () => {
    // A re-export shim that acquires logic is a third implementation wearing a
    // forwarding file's name. Body = anything that is not an import, an export
    // statement, a comment or blank.
    const suspects = [
      ...shims("projects", "lib/cursor"),
      ...shims("projects", "components/QuickAdd"),
      ...shims("projects", "components/useFlash"),
      ...shims("tasks", "lib/statusAccent"),
      ...shims("tasks", "components/ContextMenu"),
    ];
    expect(suspects.length, "no shims found — the seam moved").toBeGreaterThan(0);

    const fat = suspects.filter((f) => {
      const body = read(f)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "")
        .replace(/^\s*(import|export)\b[\s\S]*?(;|\n)/gm, "")
        .trim();
      return body.length > 0;
    });
    expect(
      fat,
      "A re-export shim grew a body. Put the logic in the shared module.",
    ).toEqual([]);
  });
});

describe("neither task app re-declares the colour palette", () => {
  /**
   * Scoped to the two task apps and the shared layer, which is what WS-27ad
   * reconciled.
   *
   * `app/crm/lib/board.ts` holds a THIRD name→class map (gray/blue/green/
   * amber/violet, for pipeline stages). It is out of this ticket's scope and
   * recorded rather than silently swept in: the CRM's stages are a different
   * axis, its map has only a `dot`, and folding it in is a CRM decision, not a
   * side effect of a Projects↔Tasks continuity pass. When somebody takes it,
   * delete this exemption rather than widening the regex.
   */
  const SCOPE = /^(app\/(projects|tasks)\/|lib\/|components\/)/;

  it("only the shared module maps a hue name to classes", () => {
    // The specific regression: `app/tasks/lib/stageColors.ts` and
    // `app/projects/lib/tags.ts` each held their own `Record` of tailwind
    // classes per colour name, and they disagreed about gray and violet.
    const offenders = FILES.filter(
      (f) =>
        f !== "lib/statusAccent.ts" &&
        SCOPE.test(f) &&
        /\b(?:gray|grey|amber|violet)\s*:\s*["'`][^"'`]*\b(?:bg|text|border)-/.test(
          read(f),
        ),
    );
    expect(
      offenders,
      "A second palette keyed by colour name. Use `statusAccent()` — a tag " +
        "and a status lane that both say 'green' have to BE the same green.",
    ).toEqual([]);
  });
});

/**
 * S1 — the card shell's props are USED, and the boards agree about the column.
 *
 * ## Why these are source scans and not render tests
 *
 * The obvious fence for "a caller passes `completed`" is to render the card and
 * look for the strike-through. This runner cannot: `vitest.config.ts` sets
 * `environment: "node"` and `include: ["src/**\/*.test.ts"]` — no DOM, and
 * `.tsx` test files are not even collected. Adding jsdom and a rendering library
 * to fence one prop is a bigger change than the thing being fenced, so these
 * read the source, exactly as the rest of this file and `conformance.test.ts`
 * do. **What they can prove is that the wiring exists; what they cannot prove is
 * what it looks like.** The second half is `DESIGN_SYSTEM.md` §8 — switch the
 * theme and look at both boards.
 */
describe("the shared card shell is wired, not merely imported", () => {
  /**
   * Source with its comments removed.
   *
   * Every scan below looks for a class name, and the comment that explains why
   * a class was REMOVED contains it — the first run of this block failed on the
   * note in `app/tasks/components/TaskBoard.tsx` saying it no longer draws
   * `rounded-xl`. A gate that a code comment can trip is a gate that teaches
   * people not to comment. (`conformance.test.ts` learned the same thing about
   * `hsl(…)` in a comment; the `//` guard here is its lookbehind, which keeps
   * a `https://` out of it.)
   */
  const code = (rel: string) =>
    read(rel)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

  /**
   * Every `<TaskCardShell …>` opening tag in the tree, with its file.
   *
   * Brace-aware rather than `/<TaskCardShell[\s\S]*?>/`, because the lazy regex
   * stops at the `>` of the first arrow function in the attribute list — so it
   * would read a tag as ending at `onActivate={() =>`, and whether the props
   * below were seen would depend on the order somebody happened to write them
   * in. That is a fence that passes for the wrong reason.
   */
  const shellTags = (): { file: string; tag: string }[] => {
    const out: { file: string; tag: string }[] = [];
    for (const file of FILES) {
      const text = code(file);
      for (const m of text.matchAll(/<TaskCardShell\b/g)) {
        let depth = 0;
        let i = m.index + m[0].length;
        for (; i < text.length; i++) {
          const c = text[i];
          if (c === "{") depth++;
          else if (c === "}") depth--;
          else if (c === ">" && depth === 0) break;
        }
        out.push({ file, tag: text.slice(m.index, i) });
      }
    }
    return out;
  };

  it("something actually renders it", () => {
    // Guards the shape of every assertion below: a regex that matches nothing
    // passes every `every()` in this block.
    expect(shellTags().length).toBeGreaterThan(1);
  });

  /**
   * The bug this exists for: `TaskCardShell` grew `completed` for /projects, and
   * for months **nothing under `src/app/tasks/` passed it** — so a finished task
   * was dimmed and struck through on one board and drawn like live work on the
   * other, from one component, with every test green. A prop that only one of
   * two callers sets is a divergence with a shared component's name on it.
   */
  it.each(["completed", "atCursor"])(
    "every caller says whether the card is %s",
    (prop) => {
      const missing = shellTags()
        .filter(({ tag }) => !new RegExp(`\\b${prop}=`).test(tag))
        .map(({ file }) => file);
      expect(
        missing,
        `These render a task card without telling it \`${prop}\`, so the shell ` +
          "falls back to `false` and the surface silently loses the treatment " +
          "the other board has. If a surface genuinely has no keyboard cursor " +
          "or no notion of done, pass the literal and say why — do not drop " +
          "the prop, which is indistinguishable from forgetting it.",
      ).toEqual([]);
    },
  );

  /**
   * The cursor ring belongs to the shell (`atCursor`), and the /tasks board used
   * to re-draw it on a wrapper div of its own — the ring then sat a pixel off
   * the card's own radius, and the two boards had two implementations of one
   * signal. List and table surfaces are deliberately NOT in scope: their rows
   * are not cards and draw `ring-inset` on a `<tr>`/`<li>` themselves.
   */
  it("no board re-implements the card cursor ring", () => {
    const BOARDS = [
      "app/tasks/components/TaskBoard.tsx",
      "app/projects/components/TaskBoard.tsx",
    ];
    for (const f of BOARDS) expect(FILES, `${f} moved`).toContain(f);
    const offenders = BOARDS.filter((f) => /ring-ring/.test(code(f)));
    expect(
      offenders,
      "Pass `atCursor` to TaskCardShell instead. A second copy of the ring is " +
        "how the two boards stopped agreeing about where the cursor is.",
    ).toEqual([]);
    expect(
      read("components/TaskCardShell.tsx"),
      "…and the shell has to still draw it.",
    ).toMatch(/ring-ring/);
  });

  /**
   * The column chrome, held equal.
   *
   * ⚠️ This is NOT a theming rule, and it would be wrong to write it as one.
   * `AGENTS.md` rule 6 says `rounded-xl` "is a fixed 12px that ignores
   * Graphite's 0.125rem" — in this tree that is **false**: `src/app/globals.css`
   * derives the whole `--radius-*` scale from `--radius` inside `@theme`, and
   * `--radius-xl` is literally `var(--radius)`, i.e. the same value
   * `rounded-lg` resolves to. A tree-wide `rounded-xl` ratchet would therefore
   * baseline ~274 occurrences that are all correctly themed.
   *
   * What was actually wrong is narrower and is what this checks: the two boards
   * drew one object — a column of task cards — at two radii on two surfaces
   * (`rounded-xl` + `bg-secondary/30` vs `rounded-lg` + `bg-card`). /projects is
   * canonical, so both are `rounded-lg` on `bg-card` now, and a fixed-radius
   * class in either file is a re-divergence.
   */
  it("both boards draw their columns with the same radius", () => {
    const offenders = [
      "app/tasks/components/TaskBoard.tsx",
      "app/projects/components/TaskBoard.tsx",
    ].filter((f) => /\brounded-(?:xl|2xl|3xl)\b/.test(code(f)));
    expect(
      offenders,
      "The two boards' columns have to be the same shape — `rounded-lg`, " +
        "/projects' chrome. This is a continuity rule, not a theming one: " +
        "`rounded-xl` IS themed here (globals.css maps --radius-xl to " +
        "--radius), it is just a different corner from the board next door.",
    ).toEqual([]);
  });

  /**
   * The drop gap does not change size while a drag is in flight.
   *
   * `DropGap` used to grow from `h-1.5` to `h-3` on a `dragging` prop. One gap
   * renders above every card plus one at the end, and both boards passed
   * board-level drag state, so lifting ONE card grew EVERY gap in EVERY column:
   * a 12-card column gained 13 x 6px = 78px and the board stretched downward
   * under the cursor. The hit area is now padding that a negative margin
   * cancels, so the target is 14px and the layout contribution never moves.
   *
   * ⚠️ This reads the SOURCE. It cannot measure a rendered box — vitest runs on
   * `environment: "node"` here, with no DOM. It fences the two ways the defect
   * came back in review: a state-conditional height inside the component, and a
   * caller re-introducing the prop. A different reflow (say `py-*` made
   * conditional) would pass this and still be wrong, so the theme sweep by eye
   * is still the real gate.
   */
  it("the drop gap's geometry does not depend on drag state", () => {
    const gap = code("components/DropGap.tsx");
    expect(
      /\bdragging\b/.test(gap),
      "`DropGap` must not read drag state. Growing the gap mid-drag reflows " +
        "every column on the board at once, which is the defect this " +
        "component was fixed for.",
    ).toBe(false);
    expect(
      /\bh-3\b/.test(gap),
      "The gap's strip is `h-1.5`. `h-3` is the height it used to grow to.",
    ).toBe(false);

    const callers = FILES.filter(
      (f) => f !== "components/DropGap.tsx" && /<DropGap\b/.test(code(f)),
    ).filter((f) =>
      /<DropGap\b[^>]*\bdragging=/.test(code(f).replace(/\n/g, " ")),
    );
    expect(
      callers,
      "A board is passing `dragging` to `DropGap`. The prop is gone on " +
        "purpose — the gap is the same size at rest and mid-drag.",
    ).toEqual([]);
  });
});
