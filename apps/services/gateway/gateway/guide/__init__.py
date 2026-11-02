"""The guide, the assistant's tour (WS-OB, D167).

Spec: ``project-docs/specs/assistant_guide.md``.

* ``flags`` — the one reader of ``GUIDE_ORGS`` (GD-5a). ``GET /auth/me/shell``
  returns it as ``guide``, and the control plane shows no entry of the tour
  while it is false.
"""
