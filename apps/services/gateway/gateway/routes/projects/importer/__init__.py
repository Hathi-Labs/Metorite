"""The file importer — bring work in from another tool by an uploaded export file.

Spec: ``project-docs/specs/project_import.md`` · decision **D80** (amends D52.2)
· board **WS-41**.

The pipeline is ``upload → adapter → ImportBundle → plan → apply``. An adapter
knows one tool's file and returns one :class:`~.bundle.ImportBundle`. It never
touches the database. The writer (a later slice) knows only the bundle.

⚠️ **No module in this package opens a network connection.** D80 allows an
importer on that condition, and ``tests/unit/test_import_no_network.py`` fails
if one imports a network client. A URL found inside an export file is data. It
is never fetched.
"""
