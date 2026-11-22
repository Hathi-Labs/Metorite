# F2a

Tier: HIGH

Gate: AGENT-SAFE

## What

🔨 **BUILT, dark (2026-11-01).** The skill routes over the F1 store: `gateway/routes/studio/skills.py`, on the router of B1, so `main.py` mounts no second router. `GET` and `POST /studio/skills`, `GET /studio/skills/{id}`, `GET /studio/skills/{id}/versions/{n}`, `POST /studio/skills/{id}/versions` and `PATCH /studio/skills/{id}/state`. **Switch:** the first line of each route reads `STUDIO_ORGS`, and an org outside it gets 404. **Tenant and member:** the org is the bound session and the member is `user.email`, never request input (R5). A skill of another member or org is 404 `Not found`, as a missing skill is. **Body checks**, before the store session opens: a depth cap of 20 (A2's cap), then `check_body`, then the catalog check. Each action is a catalog id of `actions.catalog_rows()`, once, and a skill holds at most 35 (the WS-67 pack cap). Class B and C actions are legal in a draft. **Slug:** with no slug, the route makes one from the name. A slug that the member holds is 409, and the text names no slug. **States:** the route owns the table of §7. No UI, no BFF, no migration

## Done when

`test_studio_skills_routes.py`: a not-listed org gets 404 on each of the six routes, and a listed org and `*` get 200 or 201. An `organization_id` or `owner_email` in a body or a query changes nothing. A body 1500 levels deep is 422, never 500. An unknown or duplicate action is 422 with its path. An oversized body is 422. A slug conflict is 409 with no slug in the text. Each legal and each illegal move holds, and `test_state_machine_table` pins the table. `test_studio_skills_routes_r8.py` (`pytestmark = _DB_GATE`): org B and member 2 get 404 on the skill by id on every route, and do not list it. Two new versions at once give 1 and 2. A workflow of org B is not an action of org A. The unknown-action tests go red with the catalog check removed. Two moves at once give one 200 and one 409, and the test goes red without the row lock. A slug, a name or a description that Postgres refuses is 422, never 500, and writes no row
