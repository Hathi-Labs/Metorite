"""AI Studio (WS-STU). Spec: ``project-docs/specs/ai_studio.md``.

``skills_store`` is the one writer of ``studio_skills`` and
``studio_skill_versions`` (F1). ``objects_store`` is the one writer of the
typed object store: ``studio_objects``, ``studio_object_fields``,
``studio_records`` and ``studio_record_hot`` (C1). The routes are F2 and B2,
and the feature gate is J1. This package holds no route: a route goes under
``gateway/routes/``.
"""
