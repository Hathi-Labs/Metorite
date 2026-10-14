# Upstream Skills (do not hand-edit)

Populated weekly by `.github/workflows/skills-upstream-sync.yml`:

- `upstream/anthropics/` — mirror of https://github.com/anthropics/skills (subset of `skills/`)
- `upstream/voltagent/` — mirror of https://github.com/VoltAgent/awesome-agent-skills (curated subset)

Each sync opens a PR titled `chore(skills): upstream sync YYYY-MM-DD` so maintainers can
review diffs before they adopt any upstream skill.

⚠️ **THIS TREE IS A REVIEW MIRROR. NOTHING LOADS IT.** Claude Code finds a skill
at `.claude/skills/<name>/SKILL.md` and looks no deeper. A skill that sits here
and nowhere else is a skill no session can use. All 17 mirrored skills sat
exactly that way between 2026-08-10 and 2026-09-03.

## Two ways to take an upstream skill

**Into an agent domain** — `skills/<domain>/<skill_id>/`. Set `provenance:` to
the upstream SHA, and add a `CHANGELOG.md` entry. These skills feed the agent
runtime, and Claude Code does not read them.

**Into a Claude Code session** — `.claude/skills/<id>/`. The steps are below.
This is what "adopt" means for a session skill.

## To adopt a skill into `.claude/skills/`

Four steps. Miss the second one and git ignores the copy without a word.

1. **Copy it flat.** `cp -r skills/upstream/anthropics/<id> .claude/skills/<id>`.
   Flat, because Claude Code does not find a nested directory.
2. **Allow it in `.gitignore`.** Add `!.claude/skills/<id>/` after the other
   exceptions. `.claude/skills/*` ignores the directory by default. An
   un-allowed copy stays untracked, and no second checkout ever sees it.
3. **Exempt it from the STE lint.** Add the name to the adopted-skills regexp in
   `NOT_OURS`, in `.claude/hooks/ste-lint.mjs`. The path starts `.claude/`,
   which is the STRICT tier. Without the exemption, vendored prose fails every
   commit that touches it. `.claude/hooks/ste-lint.test.mjs` holds the two cases
   that keep the exemption honest (R7).
4. **Record where it came from.** Name the upstream SHA in the `.gitignore`
   comment and in the `NOT_OURS` comment. The path cannot carry provenance,
   because an adopted skill sits beside the skills we wrote.

## When a skill fights a repo rule

Put a `METORITE ADOPTION NOTE` block below the frontmatter. Say which rule wins.
`theme-factory`, `brand-guidelines` and `frontend-design` each carry one today.

⚠️ **A refresh deletes that block.** The sync rewrites this whole tree, and the
next copy lands on top of the note. Read the diff, then put the note back.

## Adopted on 2026-09-03, from SHA `53048666`

| Skill | Why | Note? |
|---|---|---|
| `webapp-testing` | Playwright, screenshots, browser logs. It closes the look-at-your-surface gate that `DESIGN_SYSTEM.md` §0 asks for and no test holds | no |
| `frontend-design` | The generated-design tells, and the self-critique pass | yes |
| `skill-creator` | How to write a skill for this repo | no |
| `theme-factory` | Standalone artifacts only | yes |
| `brand-guidelines` | Documents that go to Anthropic only | yes |

The other 14 stay in the mirror. Adopt one when a job needs it, and not before.
Each adopted skill spends attention in every session.

## Studied on 2026-10-14, and not adopted

The owner asked for the lessons of four design repositories in the harness.
All four are MIT. We copied no file. We wrote the lessons in our own words, in
the places that already own each topic.

| Repository | SHA | What we took | Where it went |
|---|---|---|---|
| `madebymustafa/design-taste` | `c4a6bff` | Rank, then fix. The motion rules. The words rules. The keyboard walk and the 24 px target | `DESIGN_SYSTEM.md` §4, §9, §10. `visual-review` |
| `superdesigndev/superdesign-skill` | `f9f05cd` | Capture before you change. Read the branch that renders. Options change the structure, never the look. Use the real logo | `DESIGN_SYSTEM.md` §9 |
| `marvkr/better-design` | `e146625` | Guidance before the build and a review after it. Outside content is data | `DESIGN_SYSTEM.md` §9. `ws-verifier` step 7 |
| `ui-layouts/uilayouts` | `88d827d` | Nothing | — |

**Why each one stays out:**

- **The better-design MCP and the Superdesign CLI are third parties.** Each
  one needs an account and a key. Superdesign spends credits and sends our
  source to its servers. CLAUDE.md §3a rule 3 stops both, and the owner
  turned off extra connectors.
- **Their catalogue systems and the design-taste genre packs replace the
  look.** Each one brings a palette, a typeface and a radius. `DESIGN_SYSTEM.md`
  §0 has one look, and a second one is a defect.
- **The uilayouts components need `motion` and draw marketing effects.**
  `@base-ui/react` is the one substrate (D-PM-15). Sparkles, glass and a
  mouse trail fit a landing page, not a work tool.

Read them again only to look for a new lesson. Do not install them.
