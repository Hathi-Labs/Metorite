# Groups, shared sessions, and whose authority runs the agent

> **Status:** ✅ steps 1-4 shipped, step 5 shipped in part — see §6
> **Created:** 2026-07-29 · **Last built:** 2026-08-01 (groups admin UI, pending review — owned by WS-13/Centers B)
> **Answers:** [`org_access_control.md` §10](org_access_control.md#10-handoff-multiplayer-agent-collaboration) — the handoff asked for exactly one spec covering the group primitive, `chat_session_participant`, and the authority rule, *before* shared transcripts exist and the decisions get expensive.
> **Companions:** [`docs/multiplayer/README.md`](../../docs/multiplayer/README.md) (the room model and UX), [`memory-clearance.md`](../../docs/multiplayer/memory-clearance.md) (compartments), [`agent-kinds.md`](../../docs/multiplayer/agent-kinds.md) (instancing), [`memory_architecture.md`](memory_architecture.md) §5.3 (the file-store partition, migrations 136/137).

Phase 1 of org access control shipped the resolved principal (`UserContext` +
`EffectiveAccess`), default-deny authentication, feature and agent-run gating,
and credential scoping per acting member. This spec makes the three decisions
that both remaining workstreams — multi-tenant management Phase 2 and
multiplayer collaboration — depend on, so they are made **once**.

---

## 1. The group primitive: `org_group`

One table answers "who is the sales team" for every consumer that needs it:

| Consumer | How it uses a group |
|---|---|
| Access control Phase 2 (research §5, "modules/teams") | grants and visibility scoped to a team |
| Agent instancing (`agent-kinds.md` §4) | a `team` agent's partition key — `sharing.team` names an `org_group.slug`, the instance key is `t:<slug>` |
| Session sharing (§2 below) | `group:<slug>` as a participant subject |
| Shared mailboxes (research §5, later) | `email_account_member` by group |

```sql
org_group(id UUID PK, organization_id, slug, display_name, description,
          created_by, created_at, updated_at,
          UNIQUE(organization_id, slug))

org_group_member(group_id, user_id, role ∈ (lead|member),
                 added_by, added_at, PRIMARY KEY(group_id, user_id))
```

**Decisions, with reasons:**

- **Flat, not nested.** No parent_group_id. Nested groups make every
  membership check a graph walk and every access explanation a tree — the
  admin UI's "why does this person see this" stops being answerable. The org
  has ~tens of people; flat groups with overlap cover it. Nesting can be
  added later; removing it cannot.
- **`slug` is the wire reference,** consistent with everything else: grants
  say `group:sales` exactly as `app_grants.subject` says `agent:<name>`, and
  the agent manifest's `sharing.team: "sales"` now *means* this row. One
  vocabulary (`org_access_control.md` §10.2.1: "do not invent a third").
- **`lead` is a group-scoped role, not a permission bundle.** Leads manage
  membership of their own group without `admin:members:manage`. Anything
  more (per-group feature grants) stays in the existing role/override engine.
- **Groups do not confer platform permissions.** A group is a *scoping*
  primitive (who shares this thing), not an *authority* primitive (what may
  you do). Permissions keep coming from roles + overrides. Collapsing the
  two is how permission systems become unexplainable.

## 2. Session participants and visibility

The room model (`docs/multiplayer/README.md` §4.2) says thread-as-room. The
schema change is the one §10.2.2 called for — **one change, not two**:

```sql
chat_session + visibility ∈ (private|people|org)   DEFAULT 'private'

chat_session_participant(session_id, subject, role ∈ (owner|member|viewer),
                         added_by, added_at,
                         PRIMARY KEY(session_id, subject))
```

- **`subject`** follows the `app_grants` vocabulary: an email, `group:<slug>`,
  or `org`. Group subjects expand at *read time*, so leaving the sales team
  removes you from every sales room with no fan-out write.
- **`visibility` answers discovery** ("can I find/open this session?");
  **participants answer presence** ("am I in it, and as what?"). This is
  exactly `apps.visibility` + `app_grants`, the model users already
  understand (`org_access_control.md` §2 obs. 1).
- **Backfill:** every existing session gets one `owner` row from
  `chat_session.user_id` where it holds an email; the literal `'default'`
  (pre-auth single-tenant rows) backfills as owned by the org owner
  *(historical backfill description — fine; new code follows D15/R5)*. Every
  session stays `private`, so deploying this changes nobody's access.
- `viewer` is in the schema because the share flow needs read-only invitees
  (see the transcript rule in §4) — but a viewer still **caps the room's
  clearance**, see §3.

## 3. The authority rule: intersection, visibly

§10.2.3 demanded a stated decision. Here it is:

> **A shared run acts with the intersection of every participant's resolved
> access — `EffectiveAccess.intersect()` folded over all participants,
> viewers included. The person who sent the message is the attributed actor,
> not the authority.**

Concretely, at run start the executor resolves each participant
(`resolve_access(email)`, group subjects expanded, suspended members
resolving to nothing and therefore *removing* the room's access until they
are removed from the room), folds `intersect()` across the set, and that
combined access drives the two enforcement points that already exist:

- `_integration_authorizer` — credentials that any participant lacks are
  never resolved into the run env. A member without `integrations:use:zoho-crm`
  in the room means the room's agent cannot reach Zoho, *for anyone*.
- `assert_can_run_agent` — the agent must be runnable by **all** participants
  (i.e. checked against the intersection), not just the typer.

**Why intersection and not actor-authority.** Actor-authority ("whoever
typed") is the default nobody chose, and it leaks by construction: the output
of the permitted member's Zoho query lands in a transcript the denied member
reads (§10.2.4 case 2). Intersection is the only rule where *presence in the
room* never grants anyone a read they don't hold. It is also the rule the
memory design already chose independently — "a run reads at the clearance of
its least-cleared viewer" (`memory-clearance.md` §3.3) — and the rule
`intersect()` was built and tested for. Three designs converging on the same
rule is the strongest signal available that it is the right one.

**Why viewers cap the room too.** A viewer reads the transcript, and the
transcript is where the output goes. If viewers didn't cap, "add them as
viewer" would be the one-click bypass of the whole rule. A room that must not
be capped by someone shouldn't contain them — share an exported snapshot
instead (the share flow in `mockup-share.html`).

**The cap must be visible, not silent.** Intersection's failure mode is
mystery: "the agent could do this yesterday." So the room UI states the cap
as provenance, the same way the member editor does (`org_access_control.md`
§6): *"Zoho is unavailable in this room — Bob does not have access."* The
`unavailable` map already carries this to the agent; the room header carries
it to the humans. An admin fixes it by granting Bob access or the owner fixes
it by removing Bob — both visible acts, neither a silent downgrade.

**Personal memory in shared rooms.** The acting member's private memory
compartment (`_memory_user_id`) is **not injected** when the session has more
than one participant — one person's private context must not surface in
another's view (§10.2.4 case 1). The room's own compartment
(`scope_key(room=thread_id)`, `memory-clearance.md` §3.1) is what a shared
session reads and writes. Preferences (tone, formatting — the `prefs:` split)
remain per-typer and are injected as rendering hints, not content.

**Background runs are unchanged.** Cron/webhook/reconciler runs have no
participants and keep their current behaviour, for the reasons
`executor._integration_authorizer` already documents: starving them on an
unfamiliar address fails silently. Who may *start* those runs is enforced
upstream.

## 4. The transcript boundary

The remaining §10 collision (case 4) — replaying a transcript to a *newly
added* participant — is the piece that must land **with** the room feature,
not after:

- Every `chat_message` gains `author_email` + `author_kind`
  (member|agent|system) — attribution, needed for the UI regardless.
- Tool-output messages record the clearance they were produced under (the
  intersected set's hash, not the full set). Replay to any participant
  filters: a message produced under clearance the new participant doesn't
  hold renders as a redaction stub, not content.
- Because the room runs at intersection from the moment it *becomes* shared,
  redaction stubs only ever appear for messages that predate a
  participant's joining — the "declare shared mid-conversation" flow
  (`README.md` §7.1) makes this the explicit moment the clearance drops.
- Who may change a stored turn is in `projects_ai_chat.md` §18, §19 and §20
  (WS-27bm S12, S13 and S14). A human turn changes only by its author. An
  agent reply changes only by the run that wrote it, until the fold seals it.
  No write changes a system row.
- Who may create a stored turn is in `projects_ai_chat.md` §20 (S14). A
  member creates a human turn only. The gateway creates the agent row of a
  run when the run starts. No client creates an agent row or a system row,
  so no member can put words in the model context of the room.
- The creator of a session is its owner only while the session has no
  participant row (`projects_ai_chat.md` §20.3, S14 round 3). After that,
  the rows and grants decide. An owner who removes the creator removes her.

## 5. What this does NOT change

- Roles, overrides, resolution, feature gating — untouched. Groups scope,
  they do not authorize (§1).
- Single-participant sessions — the intersection of one person is that
  person; behaviour is byte-identical to today. All of §3 activates only
  when a second subject row exists.
- Agent file/memory instancing — orthogonal and already specced
  (`agent-kinds.md`); the only binding is that `t:<team>` keys now name
  `org_group.slug`.
- The quarantine and instance partition work (migrations 136/137) — already
  landed; this spec consumes it.

## 6. Order of work

| # | Step | State |
|---|---|---|
| 1 | Migration 138: `org_group`, `org_group_member`, `chat_session.visibility`, `chat_session_participant` + backfill | ✅ |
| 2 | Wire agent instancing through the run path (files + disk together; `agent_paths.py` is the seam) | ✅ |
| 3 | Participant resolution + `intersect()` fold at run start; feed the two existing enforcement points | ✅ |
| 4 | `chat_message` authorship + clearance tag; replay filter (migration 139) | ✅ |
| 5 | Room UX: share flow, participant list, the visible cap, groups admin UI | ◐ — see below |

**What step 5 shipped.** `gateway/rooms.py` (`resolve_room_access` — the one
predicate that replaced `WHERE user_id = :uid` everywhere, including
`_thread_owner_ok`), `gateway/room_stream.py` (`cc:room:{tid}` + presence),
`gateway/routes/rooms.py` (participants, agents, settings, presence, the live
room stream, the share directory), and the workbench surfaces: room header with
the visible cap, share sheet, presence rail, attributed turns, redaction stubs,
the shared badge in the sidebar, and a watcher's composer that explains itself.
Migration 139 also added `chat_session_agent`, so a room holds more than one
agent — §7 below.

**What step 5 did NOT ship, and must not be assumed:**

- **Floor control.** `chat_session.floor_mode` exists and defaults to `'open'`,
  which is today's behaviour. Nothing enforces `'driver'`; the only protection
  against two people driving one thread remains the concurrent-run 409
  (`agent.py _refuse_if_another_run_is_active`). The baton, the turn queue,
  the observer lane and `moderated` mode are designed
  (`README.md` §5) and unbuilt.
- **Steer.** Non-destructive mid-run guidance (`README.md` §4.6) is unbuilt, so
  the only way to redirect a run is still to supersede it.
- ✅ **The room's memory compartment** (2026-07-30). A shared room reads
  `room:<thread_id>` + the typer's `prefs:` + agent + org, and writes what it
  learns to the room. No participant's private compartment is read — the scope
  key is never passed to `search()`, so there is nothing to leak rather than
  something the model is asked to ignore. `acb_memory/compartments.py`.
  Still open in `memory-clearance.md`: `subject:` compartments and the
  registry, and the `prefs`/`user` backfill (so `prefs:` is empty for now).
- ✅ **Groups admin UI** (2026-08-01, pending owner review). Gateway
  `/admin/groups` — list with members, CRUD, membership add/remove with the
  `lead` role — in `gateway/routes/admin/groups.py`, plus the
  `/settings/groups` admin surface ("Teams") and a seed migration for the six
  Center groups. Owned by `work_plan.md` WS-13 (Centers Phase B); details and
  the center-feature grant semantics live in `department_centers.md` §3
  Phase B. Groups still only SCOPE — the admin surface writes membership,
  never permissions (the one bridge, the opt-in `center.<slug>` feature grant
  on add, goes through `user_permission_override` with provenance and its own
  `admin:access:manage` gate).

## 7. Several agents in one room

The room model above is about several *people*. The question it left open —
several *agents* — is answered by `chat_session_agent` (migration 139) and one
rule:

> **A room's turn is answered by exactly one agent: the primary, unless the
> message opens with `@name`.**

- `chat_session.agent_name` keeps its meaning and is the room's **primary**
  agent, so every existing read path (history, workspace, avatars, the run
  trace) resolves an agent exactly as before. `chat_session_agent` is additive:
  the others a member may address.
- Addressing is forgiving but never ambiguous (`agent.py _match_mention`):
  `@sales` finds `agent-sales-assistant`; a prefix that matches two agents
  falls through to the primary rather than guessing which colleague's agent to
  spend money on.
- **Not broadcast.** A message that reached every agent in the room would
  produce N answers to one question at N times the cost. Multi-agent rooms work
  by addressing.
- Agents are deliberately **not** `chat_session_participant` rows. Participants
  are access principals whose `EffectiveAccess` the intersection folds; an
  agent has none of its own — it acts at the room's. Putting agents in that
  table would make the fold either fail on an unknown subject or, worse, treat
  an agent as a person with no access and zero every room.
- Adding an agent checks `assert_can_run_agent_in_session`, i.e. against the
  intersection, not just the caller. An agent only *this* member may run would
  otherwise be addable and then refuse mid-run for no visible reason.
- **Agent-to-agent** work already existed as delegation (`call_agent` →
  `SUB_AGENT_*` events nested under the delegating tool call). What it lacked
  was visibility outside the tab that started it; delegated runs now publish
  activity events carrying `delegated_by`, so the live feed reads "asked by
  agent-reconciler" where a human-started run reads a person's email.
