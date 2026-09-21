"""People Center · structured skills & credentials, the gated door (WS-28h).

Spec: ``project-docs/specs/people_center_app.md`` §3.3 · **D-PC-6**.

    GET /people/{id}/skills        → structured rows + credentials (HR tier, or self)
    PUT /people/{id}/skills        → replace the skills (admin, or the subject)
    PUT /people/{id}/credentials   → replace the credentials (same rule)

The self-service twins live on the ungated router (``selfservice.py``),
resolving the person through the self predicate — the same two-door shape every
People write takes. Both doors call :mod:`gateway.person_skills`, which is the
ONE implementation and the one place the flat ``skills[]`` projection is
rewritten (D-PC-6); nothing in this module touches ``people`` directly.

**Reads are HR tier.** §3.3 marks the capability half H: levels, years and
evidence are exactly what the capability search ranks on, and the oracle rule
(§4.2) applies. The bare word list stays directory tier on the person payload,
unchanged.

**Writes are the skills field class.** ``authorize_write(["skills"], …)`` — the
same question the flat PATCH already answers, asked of the same authority. A
second authorization vocabulary for the same fact is how two answers drift.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.person_skills import (
    CREDENTIAL_KINDS,
    EVIDENCE,
    SKILL_LEVELS,
    fetch_credentials,
    fetch_skills,
    replace_credentials,
    replace_skills,
)
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    can_read_hr_fields,
    is_self,
    router,
)
from gateway.routes.people.fields import authorize_write
from pydantic import BaseModel
from sqlalchemy import text


class SkillIn(BaseModel):
    skill: str
    level: str | None = None
    years: float | None = None
    last_used_year: int | None = None
    evidence: str | None = None


class CredentialIn(BaseModel):
    kind: str
    title: str
    issuer: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    detail: str | None = None


class SkillsWrite(BaseModel):
    rows: list[SkillIn]


class CredentialsWrite(BaseModel):
    rows: list[CredentialIn]


async def _row(db: Any, person_id: str) -> Any:
    row = (await db.execute(
        text("SELECT id, email FROM people WHERE id = CAST(:id AS uuid)"),
        {"id": person_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person")
    return row


async def capability_payload(db: Any, person_id: str) -> dict[str, Any]:
    """Rows + credentials + the vocabularies, so the editor renders without a
    second round trip — the same shape the directory takes for statuses."""
    return {
        "skills": await fetch_skills(db, person_id),
        "credentials": await fetch_credentials(db, person_id),
        "levels": list(SKILL_LEVELS),
        "evidence": list(EVIDENCE),
        "credential_kinds": list(CREDENTIAL_KINDS),
    }


@router.get("/{person_id}/skills")
async def get_skills(
    person_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        row = await _row(db, person_id)
        if not (can_read_hr_fields(user)
                or is_self(user, getattr(row, "email", None))):
            raise HTTPException(
                status_code=403,
                detail="Structured skills are HR tier: admin:members:read, "
                       "or your own row.")
        return await capability_payload(db, person_id)


@router.put("/{person_id}/skills")
async def put_skills(
    person_id: str, body: SkillsWrite,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        row = await _row(db, person_id)
        authorize_write(["skills"], is_admin=can_manage_people(user),
                        is_self=is_self(user, getattr(row, "email", None)))
        await replace_skills(db, person_id,
                             [r.model_dump() for r in body.rows],
                             getattr(user, "email", None) or "anonymous")
        return await capability_payload(db, person_id)


@router.put("/{person_id}/credentials")
async def put_credentials(
    person_id: str, body: CredentialsWrite,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        row = await _row(db, person_id)
        # The same field class as skills, deliberately: "may this caller
        # describe this person's capability" is one question, and a credential
        # is capability history. A separate class would be a second answer.
        authorize_write(["skills"], is_admin=can_manage_people(user),
                        is_self=is_self(user, getattr(row, "email", None)))
        await replace_credentials(db, person_id,
                                  [r.model_dump() for r in body.rows],
                                  getattr(user, "email", None) or "anonymous")
        return await capability_payload(db, person_id)
