"""AI Studio (WS-STU). Spec: ``project-docs/specs/ai_studio.md``.

``skills_store`` is the one writer of ``studio_skills`` and
``studio_skill_versions`` (F1). The routes are F2, and the feature gate is
J1. ``packages_store`` is the one writer of a Studio package, a row of
``apps`` with ``format = 'manifest'`` (A3). This package holds no route: a
route goes under ``gateway/routes/``.
"""
