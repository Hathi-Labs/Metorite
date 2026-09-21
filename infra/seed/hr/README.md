# HR seed data (people & capabilities)

Snapshot of `FracktalWorks/agent-project-manager` → `agent-data/`
(`hr_structure.json` + `resume_profiles.json`), used to populate the
Task Manager's `people` org-knowledge layer (spec §6.1) via
`scripts/import_hr_people.py`.

- **Source of truth stays agent-project-manager / the HR system** — re-copy
  the files and re-run the import to refresh; the import upserts by name.
- Personal **phone numbers are stripped** from this snapshot on purpose; the
  task manager only needs role/skills/capacity/ClickUp id for delegation.
