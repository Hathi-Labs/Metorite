# Skills (Anthropic `SKILL.md` format · ADR-013)

This folder holds the agent-domain skills. A Claude Code session skill lives in `.claude/skills/` instead.

Layout per skill:
```
skills/<domain>/<skill_id>/
  SKILL.md          # YAML frontmatter + Markdown instructions
  scripts/          # Python scripts executed in E2B sandbox (Phase 2.9)
  tests/            # Unit tests
  evals/            # Promptfoo + Inspect AI cases (Phase 1.9 CI gate)
  CHANGELOG.md
```

See [`examples/hello_skill`](examples/hello_skill) and architecture doc §11.
