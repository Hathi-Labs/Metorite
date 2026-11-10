# Skills

## Purpose
Skill definitions and SKILL.md patterns. Each skill is a domain-specific capability.

## Skill Structure
- skills/{category}/{skill_name}/SKILL.md -- skill instructions
- skills/{category}/{skill_name}/scripts/ -- Python scripts
- Categories: delivery/, maintenance/, reconciler/, sales/, triage/
  (maintenance/ = dev-velocity skills that operate ON this codebase, for
  example codebase_health_audit)

## Conventions
- SKILL.md has YAML frontmatter with name and description
- Scripts are called via subprocess by agent tool functions
- No credentials in skill files -- Integration Registry only

## Verification
- Each SKILL.md must have valid frontmatter
- Scripts must be importable and have docstrings
