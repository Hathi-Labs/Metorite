"""Who else sees a private install (WS-PKG PKG-5b, ``package_host.md`` §5.1).

A share with named people stays ``private`` (decision 3). The people get rows
in ``app_grants``, and the resolve shows the install to each grantee. Not
each row of ``app_grants`` is a share of the install, so one rule decides. A
row ``g`` shares the private install ``package_install`` only when all three
tests hold:

1. ``g.subject`` is an email address: it holds an ``@``. A grant to
   ``agent:<name>`` or to ``agents:*`` names no person.
2. ``g.subject`` is not the owner of the install, in any case.
3. The owner made the grant: ``lower(g.granted_by)`` is the ``installed_by``
   of the install. The CHECK ``package_install_private_installer`` makes the
   owner of a private install its installer.

**No route writes an ``app_grants`` row for a Studio package today.**
``routes/apps/grants.py`` serves App Workshop apps only. Its
``get_app_or_404`` calls ``load_app``, which skips ``format = 'manifest'``,
so each grant route answers 404 for a Studio package. PKG-11, the promotion
path of ``ai_studio.md`` J2, owns the writer of a share.

Test 3 refuses each row that the owner did not make. One is a consent row:
the App Workshop consent route writes the address of the member who
consents as the subject and as ``granted_by``. If such a row counted, a
colleague who consents would see the owner's private install. Test 3 also
refuses a share that another owner of the app made.

⚠️ **A requirement on the writer of PKG-11.** ``upsert_app_grant`` of
``grants.py`` runs ``ON CONFLICT (app_id, subject) DO UPDATE SET role =
excluded.role``, so it keeps the first ``granted_by``. A share by the owner
that lands on an existing row then fails test 3. That row can be the consent
row of the colleague, or the grant of another editor. So the writer of
PKG-11 sets ``granted_by`` to the sharer on a conflict, or the rule changes
in the same slice. ``project-docs/hardening_backlog.md`` PK-7 holds it.

Two callers use the rule: the member purge (``routes/admin/members.py``,
decision 5) and the resolve of PKG-5b part 2. Do not copy the SQL. The
statement that reads it names the table ``package_install`` with no alias.
The resolve adds ``AND lower(g.subject) = <the viewer>`` to
:data:`SHARE_ROW`.

Fences: ``tests/unit/test_admin_member_purge.py`` (the text and the purge
cases) and ``tests/unit/test_package_host_store_r8.py`` (each test on a real
database).
"""

from __future__ import annotations

#: The three tests of a row ``g`` of ``app_grants`` that shares the private
#: install ``package_install`` with another person, after the join on the app.
SHARE_ROW = (
    "g.app_id = package_install.app_id"
    " AND strpos(g.subject, '@') > 0"
    " AND lower(g.subject) <> package_install.installed_by"
    " AND lower(g.granted_by) = package_install.installed_by"
)

#: True when the owner shared the private install ``package_install`` with a
#: person other than the owner.
OTHERS_SEE_THE_INSTALL = f"EXISTS (SELECT 1 FROM app_grants AS g WHERE {SHARE_ROW})"
