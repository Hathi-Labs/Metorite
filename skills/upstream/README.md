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
