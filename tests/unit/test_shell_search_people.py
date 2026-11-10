"""NS-4a, done-when 3, the people provider: a record the member cannot read
never returns through the command bar.

For the directory, "cannot read" means the HR half. A member without
``admin:members:read`` may find a colleague by name, title or department, and
never by a skill (`people_center_app.md` §3.1, "the search box cannot become an
oracle for the field the projection hides"). The rule lives inside
``list_directory``, so this runs the WHOLE shell route over the directory's own
recording fake and reads the SQL it actually ran.
"""

from __future__ import annotations

import asyncio

from gateway.routes.shell import search as shell

# The directory suite's fake database, its autouse binding and its principals.
# Imported fixtures are used by name, so the import is load-bearing.
from tests.unit.test_people_directory import (  # noqa: F401
    bind,
    db,
    hr_reader,
    person_row,
    plain,
)


def run(q: str, user) -> dict:
    return asyncio.run(shell.shell_search(q=q, scope=None, user=user))


def _directory_sql(fake) -> str:
    return " ".join(s for s in fake.statements if "FROM people" in s)


def test_a_member_without_hr_never_matches_a_colleague_by_skill(db):  # noqa: F811
    db.people = [person_row(name="Rahul", skills=["Cryptography"])]
    run("cryptography", plain())
    sql = _directory_sql(db)
    assert sql, "the people provider did not run"
    assert "unnest(skills)" not in sql


def test_an_hr_reader_does_match_by_skill_so_the_check_above_means_something(db):  # noqa: F811
    db.people = [person_row(name="Rahul", skills=["Cryptography"])]
    run("cryptography", hr_reader())
    assert "unnest(skills)" in _directory_sql(db)


def test_the_bar_shows_no_skill_and_no_hr_field(db):  # noqa: F811
    db.people = [person_row(name="Rahul", title="Firmware lead", skills=["Cryptography"],
                            resume_summary="secret summary")]
    answer = run("rahul", plain())
    items = [i for g in answer["groups"] if g["app"] == "people" for i in g["items"]]
    assert items == [{"kind": "person", "title": "Rahul", "hint": "Person · Firmware lead",
                      "href": "/people/11111111-1111-1111-1111-111111111111"}]
    flat = repr(answer)
    assert "Cryptography" not in flat and "secret summary" not in flat
