"""The per-org rollout flags of `Settings`, sorted by name, one block each.

CIV-33 slice 3 (task 3284, docs/merge_hotspots.md). Every `*_orgs` and `*_modes`
field of `Settings` lives HERE, and only here. A new flag goes at its sorted place.
Two flag PRs then edit different hunks, unless their names are neighbours. A flag
that is not a per-org list (a switch, a limit, a secret) stays in `settings.py`.

The reload code takes the names of these fields (`gateway.flag_reload.RELOADABLE`,
by the pattern `[a-z0-9_]+_(orgs|modes)`). `Settings` inherits them, so
`Settings.model_fields` holds each one, and each reader is unchanged.

Fence: tests/unit/test_flag_registry.py.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class FlagRegistry(BaseSettings):
    """The `*_orgs` and `*_modes` fields. `Settings` extends this class."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Forms of address (WS-63 PZ-40, personalization.md §16.4) ────────────
    #
    # ADDRESS_FORMS_ORGS: a comma list of organization ids, or `*`. Empty: off,
    # the default. On, the name question of the interview offers "just my name"
    # first and then the forms that fit the member's language, and the profile
    # block tells the chat to use `address.name` exactly as written. The
    # profile switch must be on too. The one reader is
    # `acb_common.assistant_profile.address_forms_for`. A SIGHUP reload applies
    # a change (`gateway/flag_reload.py`). It calls no model.
    # Fences: tests/unit/test_honorifics.py.
    address_forms_orgs: str = ""

    # EA-10p (EA3-27, spec §10.6): the organizations that may connect an
    # agent while the flag is on. Ids with a comma between them, or `*` for
    # each organization. Empty allows none, so the default is fail-closed.
    # `acb_auth.agent_oauth.org_allowed` is the one reader. The gateway and
    # the edge both read it, so both units need the same value. 🔴 A change
    # on a box is gate `enforcement-flip`.
    agent_access_orgs: str = ""

    # EA-17c-1 (D-EA-13, spec §10.8): the organizations where a member may
    # change the grant of their own agent connection, through
    # `PATCH /agent-access/connections/{id}`. The same shape as
    # `agent_access_orgs`: ids with a comma between them, or `*`. Empty, the
    # default, keeps the route dark, so it answers 404. Only the gateway
    # reads it, at each call, so a SIGHUP reload applies a change
    # (`docs/flag_reload.md`). `acb_auth.agent_oauth.change_access_allowed`
    # is the one reader. 🔴 A change on a box is gate `enforcement-flip`.
    agent_change_access_orgs: str = ""

    # ── Guidance for agents (WS-MCP SW-31a, agent_coordination.md SW-31) ────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, the `/agent-guidance` routes answer 404 and the MCP
    # answers of `get_task_context`, `claim_task` and `my_work` carry no
    # `guidance` key. The one reader is
    # `gateway.routes.agent_guidance.guidance_allowed`. A SIGHUP reload applies
    # a change (`gateway/flag_reload.py`). Fence:
    # tests/unit/test_agent_guidance.py.
    agent_guidance_orgs: str = ""

    # ── The policy store of agent work (WS-MCP SW-94, agent_coordination.md
    #    §3.11.5 and SW-94) ───────────────────────────────────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, `POST /agent-policies` answers 404, and `policy_for`
    # and `policy_for_actor` give the shipped default under the master flag of
    # each key. The one reader is
    # `gateway.routes.agent_policies.policies_allowed`. A SIGHUP reload applies
    # a change (`gateway/flag_reload.py`). Fence:
    # tests/unit/test_agent_policies_r8.py.
    agent_policy_orgs: str = ""

    # SW-56 (agent_coordination.md SW-56): the organizations where the results
    # of `claim_task` and `my_work` carry the `protocol` block, up to six short
    # lines of fixed server text. The same shape as `agent_access_orgs`: ids
    # with a comma between them, or `*`. Empty, the default, leaves the key
    # out. Only the edge reads it, at each call, through
    # `acb_auth.agent_oauth.protocol_allowed`. 🔴 A change on a box is gate
    # `enforcement-flip`.
    agent_protocol_orgs: str = ""

    # ── Questions for me (WS-55 EA-18a, external_agent_access.md §12.8) ──────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, `GET /projects/agent-questions` answers 404, the
    # creator joins no `agent_question` audience, and any member who sees the
    # task may answer (D-EA-10 point 1). On, all five points of D-EA-14 apply.
    # The one reader is `gateway.routes.projects.filters.agent_questions_allowed`.
    # A SIGHUP reload applies a change. A flip is OWNER-GATE (`enforcement-flip`).
    # Fence: tests/unit/test_agent_questions_r8.py.
    agent_questions_orgs: str = ""

    # `agent_registry_orgs` (2026-10-11, found in the WS-54 IN-7 review) lists
    # the organization ids that may change the agent registry: register, pull,
    # delete and patch on `/agent`. Grammar of `crm_orgs`: ids with a comma
    # between them, or `*`. Default empty means NO organization (fail closed).
    # The registry is deployment-wide, a register clones a repo with the
    # operator's GITHUB_TOKEN, and its code runs inside the shared gateway. So
    # only the platform operator's organization belongs here. The one reader
    # is `gateway.routes.agent._require_registry_operator`. Fence:
    # tests/unit/test_agent_registry_operator_only.py.
    agent_registry_orgs: str = ""

    # SW-34a (agent_coordination.md SW-34a, external_agent_access.md §9.3): the
    # organizations where the edge serves MCP resources (`resources/list`,
    # `resources/templates/list`, `resources/read`). The same shape as
    # `agent_access_orgs`: ids with a comma between them, or `*`. Empty, the
    # default, keeps them dark: the list is empty and a read is not found.
    # `acb_auth.agent_oauth.resources_allowed` is the one reader, at each call.
    # 🔴 A change on a box is gate `enforcement-flip`.
    agent_resources_orgs: str = ""

    # ── Agents: the member view in Projects (WS-AGA AGA-6) ──────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, every route of `agents_view.md` answers 404, today
    # `GET /projects/agents/overview` (AGA-7). The one reader is
    # `gateway.routes.projects.agent_summary.agents_view_allowed`. A SIGHUP
    # reload applies a change. A flip is OWNER-GATE (`enforcement-flip`).
    # Fence: tests/unit/test_agents_overview_r8.py.
    agents_view_orgs: str = ""

    # ── The trigger of the project alignment check (WS-ALN ALN-1, D160) ─────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. A task write of a listed org enqueues one `align.check`
    # job, and only while `job_queue` is on too. `prune` deletes no queued
    # row, so a queue with no worker would grow without a bound
    # (`project_alignment.md` §5 item 8). The one reader is
    # `gateway/align/trigger.py::jobs_enabled`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). The handler of ALN-1 is a stub, so a
    # flip spends nothing, and it is gate `enforcement-flip`. Fence:
    # tests/unit/test_align_trigger.py.
    align_jobs_orgs: str = ""

    # ── The app requests of agents (WS-MCP MCP-P1b-1) ──────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, the four request routes of `/agent-access` answer 404,
    # and Integrations, AI agents shows no list "Waiting for you". It is the
    # dark switch of the approval store, because `AGENT_ACCESS_ENABLED` is on in
    # production. The one reader is `gateway.app_requests.requests_allowed`.
    # A SIGHUP reload applies a change. Only the owner flips it, under a dated
    # `enforcement-flip` grant, after MCP-P1b-2 or STU-B2c-2 can ask. Fence:
    # tests/unit/test_app_requests_r8.py.
    app_requests_orgs: str = ""

    # ── The briefing at the start of a chat (WS-63 PZ-34, D142) ─────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. It opens the block of PZ-34: the newest summaries of the
    # member's other private chats and their tasks that are due
    # (`personalization.md` §15.4). The profile switch (`enabled_for`) must be
    # on for the org too. The one reader is
    # `acb_common.assistant_profile.briefing_for`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). It calls no model. Fences:
    # tests/unit/test_executor_briefing_block.py, tests/unit/test_chat_summary.py.
    assistant_briefing_orgs: str = ""

    # ── A summary of each private chat (WS-63 PZ-33, D142) ──────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. It opens the summary job of PZ-33b and the Settings
    # section of PZ-35 (`personalization.md` §15.8). PZ-33a adds no caller.
    # The profile switch (`enabled_for`) must be on for the org too. The one
    # reader is `acb_common.assistant_profile.chat_summary_for`. A SIGHUP
    # reload applies a change (`gateway/flag_reload.py`). ⚠️ The job spends
    # the org's AI credits with no request of the member, so the production
    # flip is owner-only (D-PZ-35, `work_plan.md` §6 D4). Fence:
    # tests/unit/test_chat_summary.py.
    assistant_chat_summary_orgs: str = ""

    # ── The card writes of the assistant profile (WS-63 PZ-32) ──────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. On for an org, the web chat may ask a card for a profile
    # value that the member did not type (D-PZ-21), and the assistant may
    # offer once in a chat to keep a fact that the member said in passing
    # (D-PZ-22). Off, the typed-words rule refuses such a value, an offer
    # asks nothing, and the persona has no offer line. The one reader is
    # `acb_common.assistant_profile.card_writes_for`. A SIGHUP reload applies
    # a change (`gateway/flag_reload.py`). Fence:
    # tests/unit/test_profile_pz32.py.
    assistant_profile_card_writes_orgs: str = ""

    assistant_profile_orgs: str = ""

    # ── The authority page of a project (WS-ALN ALN-4) ───────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, the three authority routes answer 404 and the three
    # chat tools make no card. The one reader is
    # `gateway.routes.projects.authorities.authority_pins_allowed`. A SIGHUP
    # reload applies a change. A flip needs a D145 preview of the chat cards
    # (ALN-4b). Fence: tests/unit/test_project_authorities_r8.py.
    authority_pins_orgs: str = ""

    # ── Park and ask (WS-AUTO AUTO-7b, proactive_agent.md §4.2) ─────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. On, the approval gate of a workflow run with no person
    # present asks the workflow's owner through ONE decision, instead of a
    # broker proposal. `decisions_orgs` must list the org too. Off, the gate
    # files the broker proposal of AUTO-7a, byte for byte. The one reader is
    # `gateway.routes.workflows.park.park_allowed`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). Fence: tests/unit/test_auto_park.py.
    # ⚠️ The wait scan settles a parked gate only for an org on
    # `workflow_unattended_orgs`. If the org leaves that list, a yes resumes
    # nothing and no expiry ends the run, until the org is on it again.
    auto_park_orgs: str = ""

    brief_orgs: str = ""

    # ── Celebrations (WS-63 PZ-43, personalization.md §16.6) ────────────────
    #
    # CELEBRATIONS_ORGS: a comma list of organization ids, or `*`. Empty: off,
    # the default. On, the member can set a birthday (no year), festival picks
    # and the work-anniversary switch, and the first chat reply of a day on
    # which one falls opens with one wish. The profile switch must be on too.
    # The one reader is `acb_common.celebrations.celebrations_for`. A SIGHUP
    # reload applies a change (`gateway/flag_reload.py`). It calls no model.
    # Fences: tests/unit/test_celebrations.py.
    celebrations_orgs: str = ""

    # ── Continue a held chat in a fresh chat (one_assistant_context.md §9, WT-1) ─
    #
    # `chat_continue_orgs` lists the organization ids where
    # `POST /chat/sessions/{id}/continue` answers, or `*` for each one. Empty
    # means off, and the route answers 404 for each caller. The one reader is
    # `acb_common.chat_continue_flag.continue_allowed`, and it fails closed. The name
    # ends in `_orgs`, so a SIGHUP applies a change with no restart
    # (`docs/flag_reload.md`).
    #
    # 🔴 OWNER-ONLY on a box: any value other than empty (`work_plan.md` §6
    # D4, slice WT-5). The flip also waits for WT-1b and WT-2 (§9.3 item 9).
    # Fence: tests/unit/test_continue_carry.py.
    chat_continue_orgs: str = ""

    # ── A late approve acts once (HRD-22, chat_run_continuity.md S2 item 8) ──
    #
    # CHAT_LATE_APPROVE_ORGS: a comma list of organization ids, or `*`. Empty
    # is off, the default, and then each card and each message is as before.
    # On, a late Approve of a parked plain confirmation card is the member's
    # own consent: the new run's gate approves the SAME card once, in the same
    # thread, for the same member, within 30 minutes, with no new card. It
    # needs CHAT_DURABLE_ASKS. The one reader is
    # `acb_skills.late_approve.late_approve_on`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). A production flip is gate
    # `enforcement-flip`. Fences: tests/unit/test_late_approve_grant.py and
    # tests/unit/test_late_approve_grant_r8.py.
    chat_late_approve_orgs: str = ""

    # ── The line of a slow tool (HRD-6, chat_failure_modes.md §4 row B6) ────
    #
    # CHAT_PROGRESS_NOTICE_ORGS: a comma list of organization ids, or `*`.
    # Empty: off, the default, and each run sends the bytes that it sent
    # before. On, a tool call that runs for more than 6 s gets ONE line for the
    # member ("This is a large read. It can take a few minutes …" for a read,
    # "This is taking longer than usual. Please wait." for any other tool), on
    # the web chat and as WhatsApp's early message. A WhatsApp run of a listed
    # org also gets the long cap (`bot_run.run_timeout_for`). The one reader is
    # `acb_skills.progress_notice.progress_notice_on`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). It calls no model.
    # Fence: tests/unit/test_progress_notice.py.
    chat_progress_notice_orgs: str = ""

    # ── Voice input in the chat (projects_ai_chat.md §31, VOX-1) ────────────
    #
    # `chat_voice_orgs` lists the organization ids whose chat composer shows the
    # mic, or `*` for each one. Empty means off, and `/chat/transcribe` answers
    # 404 for each caller. The one reader is
    # `acb_common.chat_voice_flag.voice_allowed`, and it fails closed. The name
    # ends in `_orgs`, so a SIGHUP applies a change with no restart
    # (`docs/flag_reload.md`).
    #
    # Each clip draws AI credits through the Console's `tier-stt` door, so a
    # value other than empty on a box is an `enforcement-flip`. A real clip and
    # its usage row must be proven first (§31.5 VOX-1, Done when 4).
    # Fence: tests/unit/test_chat_transcribe.py.
    chat_voice_orgs: str = ""

    # STU-E12a: CORE_EVENTS_ORGS lists the org ids where a core app emits its
    # events on the one event bus (`people.person.updated` first). `*` opens
    # every org. Empty opens none. Reader:
    # `gateway.core_events.core_events_allowed`. A SIGHUP reload applies a
    # change (docs/flag_reload.md). A flip in production is the gate
    # `enforcement-flip`. Spec: ai_studio.md section 14 E12a.
    core_events_orgs: str = ""

    # `crm_answer_first_orgs` (WS-53 CRM-Q33, 2026-10-18) lists the
    # organization ids whose chat answers first and clarifies over time
    # (`crm_platform.md` §18.15), in the grammar of `crm_orgs`. Default empty
    # means NO organization (fail closed). An org needs a place on
    # `crm_questions_orgs` too. Off the list, the chat asks before it answers
    # (CRM-Q32-8), and the business rules, the WhatsApp question and the
    # evidence of GST stay off. The one reader is
    # `gateway.routes.crm.flags`. A name in the list spends nothing, so a
    # flip on a box is gate `enforcement-flip`. Fence:
    # tests/unit/test_crm_answer_first.py.
    crm_answer_first_orgs: str = ""

    # `crm_catalogue_orgs` (WS-53 CRM-Q31-1, 2026-10-17) lists the
    # organization ids that get the CRM catalogue for the AI
    # (`crm_platform.md` §18.13.4), in the grammar of `crm_orgs`. Default
    # empty means NO organization (fail closed). An org outside the list gets
    # 404 `catalogue_off` from `GET /crm/catalogue` in each mode, no key word
    # in the aggregate, and the old answer of `crm_list_modules`. The one
    # reader is `gateway.routes.crm.flags`. A name in the list spends
    # nothing, so a flip on a box is gate `enforcement-flip`. Fence:
    # tests/unit/test_crm_catalogue.py.
    crm_catalogue_orgs: str = ""

    # `crm_cutover_orgs` (WS-53 CRM-Z10b, 2026-10-28) lists the organization
    # ids that may switch from the Zoho mirror to Native mode
    # (`crm_platform.md` §3.4), in the grammar of `crm_orgs`. Default empty
    # means NO organization (fail closed): the three switch routes answer
    # 404. An org also needs a place on `crm_mirror_orgs`. The one reader is
    # `gateway.routes.crm.flags`. 🔴 A name on a production box is the
    # owner's production run (CRM-Z10-run). Fence:
    # tests/unit/test_crm_cutover_switch_r8.py.
    crm_cutover_orgs: str = ""

    # ── The CRM's `decide` org list (WS-53 CRM-Q26) ─────────────────────────
    #
    # `crm_decide_orgs` lists the organization ids that may run a `crm.*`
    # feature (today `crm.note_labels`) in a mode other than `off`, in the
    # grammar of `decide_feature_orgs`. For a `crm.` feature, `mode_for` reads
    # it and NOT `decide_feature_orgs`, so a mode change never starts every
    # CRM org at once (`crm_platform.md` §18.11.2 item 2). Default empty: no
    # organization. The one reader is `gateway/decide_features.py`.
    #
    # 🔴 OWNER-GATE on a box: a value in the list starts real Router calls,
    # billed to the org (`work_plan.md` §6). Fence:
    # tests/unit/test_crm_note_labels.py.
    crm_decide_orgs: str = ""

    # `crm_digest_orgs` (WS-53 CRM-Q28, 2026-10-17) lists the organization
    # ids that get the weekly CRM digest on the powerful tier
    # (`crm_platform.md` §18.11.2), in the grammar of `crm_orgs`. Default
    # empty means NO organization (fail closed). An org outside the list gets
    # no digest step after a sync run, and no model call. The one reader is
    # `gateway.routes.crm.flags`. 🔴 OWNER-GATE on a box: each digest spends
    # the org's credits (`work_plan.md` §6). The grant `enforcement-flip`
    # does not cover it. Fence: tests/unit/test_crm_digest.py.
    crm_digest_orgs: str = ""

    # `crm_field_studio_orgs` (WS-53 CRM-C4a, 2026-10-13) lists the
    # organization ids that may use the field studio of a Native CRM, in the
    # grammar of `crm_orgs`. Default empty means NO organization (fail
    # closed): each studio route answers 404. The one reader is
    # `gateway.routes.crm.flags`. 🔴 A flip on a box is gate
    # `enforcement-flip`. Fence: tests/unit/test_crm_field_studio_r8.py.
    crm_field_studio_orgs: str = ""

    # `crm_fill_rank_orgs` (WS-53 CRM-Q17a-1, 2026-10-16) lists the
    # organization ids that get the fill rank and the source labels of the
    # mirror (`crm_platform.md` §18.9.3), in the grammar of `crm_orgs`.
    # Default empty means NO organization (fail closed). An org outside the
    # list gets no fill work in the sync, and the CRM routes serve it no
    # `fill`, `distinct`, `empty_columns` or `labels`. The one reader is
    # `gateway.routes.crm.flags`. 🔴 A flip on a box is gate
    # `enforcement-flip`. Fence: tests/unit/test_crm_fill_rank_r8.py.
    crm_fill_rank_orgs: str = ""

    # `crm_long_read_orgs` (WS-53 CRM-Q25e part 5, task 2985 HRD-6,
    # 2026-11-13) lists the organization ids whose CRM totals may take a
    # long read of up to 180 s (`crm_platform.md` §18.11, CRM-Q25e), in the
    # grammar of `crm_orgs`. Default empty means NO organization: a total
    # that runs past 10 s answers 503 `aggregate_timeout`, as before, and
    # the chat tool makes no second call. The one reader is
    # `gateway.routes.crm.flags`. A `*_ORGS` key, so a SIGHUP reload applies
    # it (`docs/flag_reload.md`). A name in the list spends nothing, so a
    # flip on a box is gate `enforcement-flip`. Fence:
    # tests/unit/test_crm_large_reads.py.
    crm_long_read_orgs: str = ""

    # `crm_mirror_orgs` (WS-53 CRM-L2, 2026-10-11) lists the organization ids
    # that may use the mirror, in the grammar of `crm_orgs`: ids with a comma
    # between them, or `*`. Default empty means NO organization (fail closed).
    # The CRM is live for each organization, and the Zoho mirror is not. An
    # organization outside the list gets 404 on the mirror routes, and the
    # sync loop skips it. The one reader is `gateway.routes.crm.flags`.
    crm_mirror_orgs: str = ""

    # `crm_note_search_orgs` (WS-53 CRM-Q27, 2026-10-16) lists the
    # organization ids that get search of CRM note text by meaning
    # (`crm_platform.md` §18.11.2), in the grammar of `crm_orgs`. Default
    # empty means NO organization (fail closed). An org outside the list gets
    # no embed step after a sync run, and `GET /crm/notes/search` answers it
    # `degraded` with no embed call. The one reader is
    # `gateway.routes.crm.flags`. 🔴 OWNER-GATE on a box: each embed request
    # costs vendor money that no credit row pays (`work_plan.md` §6). The
    # grant `enforcement-flip` does not cover it. Fence:
    # tests/unit/test_crm_note_vectors.py.
    crm_note_search_orgs: str = ""

    crm_orgs: str = ""

    # `crm_pipelines_orgs` (WS-CRM CRM-M3a, 2026-11-07) lists the
    # organization ids whose admins may create a deal pipeline, from CRM
    # settings and from the chat (`crm_conversational.md` §8 CRM-M3). The
    # grammar is the one of `crm_orgs`. Default empty means NO organization
    # (fail closed): the create route answers 404, the button does not
    # show, and the chat tool leaves the run. The one reader is
    # `gateway.routes.crm.flags`. 🔴 A flip on a box is gate
    # `enforcement-flip`. Fence: tests/unit/test_crm_pipelines_native.py.
    crm_pipelines_orgs: str = ""

    # `crm_questions_orgs` (WS-53 CRM-Q32-1, 2026-10-18) lists the
    # organization ids that get the open questions of the CRM
    # (`crm_platform.md` §18.14.3), in the grammar of `crm_orgs`. Default
    # empty means NO organization (fail closed). An org needs a place on
    # `crm_catalogue_orgs` too. An org outside gets 404 `questions_off` from
    # `GET /crm/questions` and `PUT /crm/catalogue/{module}`, no questions
    # step after a sync run, no My Day card and no CRM facts. The one reader
    # is `gateway.routes.crm.flags`. A name in the list spends nothing, so a
    # flip on a box is gate `enforcement-flip`. Fence:
    # tests/unit/test_crm_questions.py.
    crm_questions_orgs: str = ""

    # ── The per-feature `decide` modes (WS-17 EM-T5, CP-13e) ────────────────
    #
    # `decide_feature_modes` holds `feature=mode` pairs with a comma between
    # pairs, for example `email.cold_check=shadow`. A mode is `off`, `shadow`
    # or `on`. An unknown feature or mode resolves to `off`. EM-T5 refuses `on`
    # and resolves it to `off`. `decide_feature_orgs` lists the organization
    # ids that may run a mode other than `off`, with a comma between ids. An
    # empty list allows no organization. The one reader is
    # `gateway/decide_features.py`.
    #
    # 🔴 OWNER-GATE on a box (H-166): any value other than empty.
    # Fence: tests/unit/test_email_decide_shadow.py.
    decide_feature_modes: str = ""

    decide_feature_orgs: str = ""

    # ── One person decides (WS-ALN ALN-2a, project_alignment.md §3.2) ───────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, the decision routes answer 404, no decision is asked
    # and no bell row is written, so nothing reaches a member. The one reader
    # is `gateway.routes.projects.decisions.decisions_allowed`. A SIGHUP
    # reload applies a change (`gateway/flag_reload.py`). Fence:
    # tests/unit/test_decisions.py.
    decisions_orgs: str = ""

    # RX-5: DUE_PUSH_ORGS lists the org ids whose members get the morning
    # summary push of due tasks. `*` opens every org. Empty opens none. It
    # reaches a real person, so a flip in production is the owner's. Reader:
    # `gateway.reach.flags.due_push_allowed`. Spec:
    # calendar_events_reminders.md section 5.11.
    due_push_orgs: str = ""

    # ── The one email card of a new mail (email_app_master_plan.md §18.10) ──
    #
    # `email_edit_card_orgs` lists the organization ids whose chat shows a new
    # mail on the ONE email card that the member edits, or `*` for each one.
    # Empty means off, and a new mail shows the cards of before: the plain send
    # card, and the rows card of a held chat. The one reader is
    # `acb_skills.reply_card.edit_card_on`, and it fails closed. The name ends
    # in `_orgs`, so a SIGHUP applies a change with no restart.
    #
    # A value on a box is an `enforcement-flip`: it changes the card of a send.
    # Name the flag and the box in the same message.
    # Fence: tests/unit/test_email_new_mail_card.py.
    email_edit_card_orgs: str = ""

    email_insights_orgs: str = ""

    # ── Email signature layouts (WS-17 SG-2, email_app_master_plan.md §23.5) ─
    #
    # `email_signature_templates_orgs` lists the organization ids whose
    # mailboxes may use a layout signature, or `*` for each one. Empty means
    # off, the default: the resolver gives the `custom` signature, which is
    # the behaviour of before, and `POST /email/signature/preview` answers
    # 404. The one reader is `gateway.routes.email.signature.templates_on`,
    # and it fails closed. The name ends in `_orgs`, so a SIGHUP applies a
    # change with no restart. A value on a box is an `enforcement-flip`: name
    # the flag and the box in the same message.
    # Fence: tests/unit/test_email_signature_resolver.py.
    email_signature_templates_orgs: str = ""

    # ── The contract of an epic (SP-8a, project_management_app.md §13.3) ────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, `GET` and `PUT /projects/tasks/{id}/epic-contract`
    # answer 404, `get_task` carries `epic_contract: null`, a create with a
    # contract makes no task, and the chat tool makes no card. The one reader
    # is `gateway.routes.projects.epic_contract.epic_contracts_allowed`. A
    # SIGHUP reload applies a change. Fence: tests/unit/test_epic_contract_r8.py.
    epic_contract_orgs: str = ""

    # ── The App for each organization (WS-MCP SW-53, agent_coordination.md) ─
    #
    # A comma list of organization ids, or `*` for each one. The master
    # `github_app_enabled` comes first: off means off for every org. With the
    # master on, an org that the list does not name is off. An EMPTY list
    # turns every org off (fail closed). Off for an org: a webhook delivery
    # for its installation answers 202 and writes nothing, the connect routes
    # answer 404, and the reconcile poll skips it. The one reader is
    # `gateway.connections.github_app.github_app_org_allowed`. A SIGHUP reload
    # applies a change. A flip is OWNER-GATE (`enforcement-flip`).
    # Fence: tests/unit/test_github_webhook_r8.py.
    github_app_orgs: str = ""

    # WS-KG KG-4 (knowledge_graph.md §4.1 and §8). The organization ids whose
    # records the graph sweep joins each night, in the grammar of
    # `knowledge_orgs`. `*` is each org. Empty, the default, means no
    # organization: the seed hook enqueues no job and the job writes no edge.
    # The one reader is `gateway.kb_entities.graph.sweep_allowed`. It calls no
    # model and spends nothing. AGENT-SAFE: a flip is gate `enforcement-flip`.
    # Fences: tests/unit/test_kg_links_writer.py, test_kg_links_r8.py.
    graph_sweep_orgs: str = ""

    # WS-OB GD-5a: GUIDE_ORGS lists the org ids whose members see the entries
    # of the guide's tour (the welcome button and the account menu row). `*`
    # opens every org. Empty opens none. Reader: `acb_common.guide_flags.
    # guide_allowed`, returned as `guide` from GET /auth/me/shell. Spec:
    # assistant_guide.md section 7.
    guide_orgs: str = ""

    # ── The headless turn (WS-AUTO AUTO-8a, proactive_agent.md §4.4) ────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. On, a job of kind `auto.turn` runs one capped turn with no
    # person present. It is the on switch and the kill switch: the turn reads
    # it again before each round. The one reader is
    # `gateway.jobs.headless_turn.headless_allowed`. A SIGHUP reload applies a
    # change (`gateway/flag_reload.py`). A turn spends the member's AI credits,
    # so only the owner flips it. Fence: tests/unit/test_headless_turn.py.
    headless_turn_orgs: str = ""

    # ── The held card writes of a chat that read outside mail (WS-67 RX-1) ──
    #
    # `held_card_writes_orgs` lists the organization ids where a held chat keeps
    # the writes that a card shows (`work_plan.md`, the decision of RX-1), or
    # `*` for each one. Empty means off, and a held chat then works as it did
    # before the slice. The one reader is `egress.held_card_writes_on`, and it
    # fails closed. It is the kill switch of the decision: the name ends in
    # `_orgs`, so a SIGHUP applies a change with no restart (`docs/flag_reload.md`).
    #
    # A value on a box is an `enforcement-flip`. It moves what a chat that read
    # mail may write, so name the flag and the box in the same message.
    # Fence: tests/unit/test_held_card_writes.py.
    held_card_writes_orgs: str = ""

    # ── Image analysis in the chat (WS-VOX VOX-3, projects_ai_chat.md §31) ──
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. On for an org, the chat tool `analyze_image` sends an image
    # that a member attached to the model of `tier-vision` through the Console,
    # and each call draws AI credits. Off, the tool answers one sentence and
    # calls no model. The one reader is
    # `acb_common.assistant_profile.image_analysis_for`. A SIGHUP reload applies
    # a change (`gateway/flag_reload.py`). ⚠️ The flip on production spends
    # credits, so it is an owner step (§31.6 Q4). Fence:
    # tests/unit/test_attachment_analyze_image.py.
    image_analysis_orgs: str = ""

    knowledge_orgs: str = ""

    # ── MCP servers on native MAF agents (WS-8c, agent_architecture.md §12.2) ─
    #
    # `MAF_MCP_ORGS` lists the organization ids whose native MAF agents get
    # the MCP servers of their org, with a comma between ids. `*` allows each
    # organization. An empty list, the default, allows none, and then a native
    # agent reads no MCP server of the registry (WS-54 IN-4). The
    # one reader is `orchestrator._maf_mcp.maf_mcp_org_allowed`, and the org it
    # checks is the run binding, never request input. `env_guard` refuses each
    # `MAF_*` name on each Integrations write. Restart the gateway after a
    # change.
    #
    # 🔴 A flip on a box is gate `enforcement-flip`. Name the org and the box.
    # Fence: tests/unit/test_maf_mcp_injection.py.
    maf_mcp_orgs: str = ""

    # ── Member code runs on the box (WS-STU STU-Q15a) ──────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, a code module of Module Studio runs for no org: the
    # two test routes answer 403 `module_runs_off`, a workflow module node
    # fails with the same message, and `/studio` shows no Module Studio tab.
    # D127.2 keeps member code off the box, and the runner is not a sandbox
    # (`ai_studio.md` §12 STU-Q15). The one reader is
    # `gateway.module_runs.module_runs_allowed`. A SIGHUP reload applies a
    # change. Only the owner flips it, after the owner answers STU-Q15.
    # Fence: tests/unit/test_module_runs_off.py.
    module_run_orgs: str = ""

    # ── My Day's `decide` org list and ceiling (WS-58 MD-6a-1) ──────────────
    #
    # `my_day_decide_orgs` lists the organization ids that may run a
    # `my_day.*` feature in a mode other than `off`, in the grammar of
    # `decide_feature_orgs`. For a `my_day.` feature, `mode_for` reads it and
    # NOT `decide_feature_orgs`, so email keeps its `*` (`my_day_feed.md`
    # §10.9 item 5). Default empty: no organization.
    # `my_day_decide_org_daily_calls` is the ceiling of `decide` calls for one
    # organization in one day. 0 sets no ceiling, and the member cap of 60
    # still binds (§9.1 rule 8, §10.9 item 6). The readers are
    # `gateway/decide_features.py` (the list) and
    # `gateway/routes/shell/feed_decide.py` (the ceiling).
    #
    # 🔴 OWNER-GATE on a box: a value in the list starts real Router calls,
    # billed to the org. Fence: tests/unit/test_feed_decide.py.
    my_day_decide_orgs: str = ""

    # SW-68a (agent_coordination.md SW-68): the organizations where `my_work`
    # (MCP and chat) ranks the tasks under an epic by the plan engine. Comma
    # list of organization ids, or `*`. Empty: off, and `my_work` keeps its old
    # order. Only the gateway route reads it, so a SIGHUP reload of the gateway
    # applies a change. The edge reads no flag. With the flag off, the edge still
    # makes one plan read (under its 2 second limit), and the answer is the
    # input order.
    # The one reader is `acb_auth.agent_oauth.plan_order_allowed`. Fence:
    # tests/unit/test_my_work_plan_order_r8.py.
    my_work_plan_order_orgs: str = ""

    # ── Outlook all-mail webhook (WS-17, task 1966) ─────────────────────────
    #
    # `outlook_all_mail_webhook_orgs` lists the organization ids whose Outlook
    # push subscription watches ALL mail (`/me/messages`), so mail that a rule
    # files into a user folder arrives at once and not at the next poll. Empty
    # means off, and the subscription watches the Inbox only. The reader is
    # `gateway.routes.email.transport.sync._all_mail_webhook_on`. The name ends
    # in `_orgs`, so a SIGHUP applies a change with no restart.
    # Fence: tests/unit/test_outlook_all_mail_webhook.py.
    outlook_all_mail_webhook_orgs: str = ""

    # WS-PKG PKG-5b: PACKAGE_HOST_ORGS lists the org ids that may read the
    # resolved shell manifest (GET /shell/manifest). `*` opens every org.
    # Empty opens none. Reader:
    # `gateway.package_host.flags.package_host_allowed`. Spec:
    # package_host.md section 10.
    package_host_orgs: str = ""

    # PB-2 (D186, `orchestration_playbook.md` §5, §7): the organizations where
    # the edge lists the playbook prompts (`set_up_agent_team`, `plan_slices`,
    # `write_brief`, `review_my_fleet`, `run_retro`). The same shape as
    # `agent_access_orgs`: ids with a comma between them, or `*`. Empty, the
    # default, keeps them dark. The edge and the gateway read it, at each call, through
    # `acb_auth.agent_oauth.playbook_allowed`. 🔴 A change on a box is gate
    # `enforcement-flip`.
    playbook_orgs: str = ""

    # ── The rules of a project (WS-ALN ALN-5a) ──────────────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, each `/project-rules` route answers 404 and no rule
    # row is written. The one reader is
    # `gateway.routes.project_rules.project_rules_allowed`. A SIGHUP reload
    # applies a change (`gateway/flag_reload.py`). Only the owner writes the
    # value. Fence: tests/unit/test_project_rules.py.
    project_rules_orgs: str = ""

    # ── The Projects `decide` org list (DEC-1) ──────────────────────────────
    #
    # `projects_decide_orgs` lists the organization ids that may run a
    # `projects.*` feature (today `projects.align`) in a mode other than
    # `off`, in the grammar of `decide_feature_orgs`. For a `projects.` feature
    # `mode_for` reads it and NOT `decide_feature_orgs`, so a mode change never
    # starts every Projects org at once. Default empty: no organization. A
    # `*_ORGS` field, so it reloads with no restart (`docs/flag_reload.md`).
    # The one reader is `gateway/decide_features.py`.
    #
    # 🔴 OWNER-GATE on a box: a value in the list starts real Router calls,
    # billed to the org (`work_plan.md` §6). Fence:
    # tests/unit/test_decide_seam.py.
    projects_decide_orgs: str = ""

    # ── The cycles of Projects (WS-39 SP-1, D195) ───────────────────────────
    #
    # The flag `PROJECTS_SPRINTS` of the board, as a per-org list
    # (project_management_app.md §13.2a). A comma list of organization ids,
    # or `*` for each one. Empty means off, the default. Off, each cycle route
    # of `/projects` answers 404 and no cycle row is written. The one reader
    # is `gateway.routes.projects.cycles.sprints_allowed`. A SIGHUP reload
    # applies a change (`gateway/flag_reload.py`). Only the owner writes the
    # value. Fence: tests/unit/test_sprint_store_r8.py.
    projects_sprints_orgs: str = ""

    # ── The templates of Projects (WS-39 TPL-1a, task 18) ──────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, each `/projects/templates` route answers 404 and no
    # template row is written. The one reader is
    # `gateway.routes.projects.templates.templates_allowed`. A SIGHUP reload
    # applies a change (`gateway/flag_reload.py`). Only the owner writes the
    # value. Fence: tests/unit/test_pm_templates_r8.py.
    projects_templates_orgs: str = ""

    # ── Project updates with health (WS-39 LP-1a, project_management_app.md §14.2a)
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, the two update routes answer 404, the two MCP tools
    # say that the feature is off, and the web line does not draw. The one
    # reader is `gateway.routes.projects.updates.updates_allowed`. A SIGHUP
    # reload applies a change. Fence: tests/unit/test_pm_project_updates_r8.py.
    projects_updates_orgs: str = ""

    # OB-8: PUSH_PROMPT_ORGS lists the org ids whose phone members see the
    # chat sheet that asks them to turn on push. `*` opens every org. Empty
    # opens none. Reader: `gateway.push.flags.prompt_allowed`. Spec:
    # calendar_events_reminders.md section 10.4.
    push_prompt_orgs: str = ""

    reach_orgs: str = ""

    # ── Dynamic sub-agents of the one assistant (WS-67 OA-9b, D117) ─────────
    #
    # `spawn_subagents_orgs` lists the organization ids whose main assistant
    # holds `spawn_subagents`, or `*` for each one. Empty means off. The one
    # reader is `acb_skills.subagent_contract.spawn_subagents_on`, and it fails
    # closed. A child only reads, but it runs model calls at the org's cost.
    #
    # 🔴 OWNER-ONLY on a box: any value other than empty (`work_plan.md` §6
    # D4). The dev-phase window does not open it.
    # Fence: tests/unit/test_spawn_subagents.py.
    spawn_subagents_orgs: str = ""

    # ── The status-set guard in chat (WS-42 PST-4, projects_settings.md §7.1) ──
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. On, the chat tools `create_status`, `update_status` and
    # `delete_status` refuse to change a status set that the project shares,
    # unless the member says so, and they send a `slot` and never a raw
    # `position`. The one reader is `gateway.routes.projects.admin.
    # status_set_guard_allowed`. It returns `guard_on` on the status-set read,
    # and the tools read that. A SIGHUP reload applies a change. A flip is
    # OWNER-GATE (`enforcement-flip`).
    # Fence: tests/unit/test_projects_status_chat.py.
    status_set_guard_orgs: str = ""

    # STU-I1a: STUDIO_CONNECTION_GATE_ORGS lists the org ids where the
    # `connections` of a Studio manifest bind. A missing connection then makes
    # the install `needs_setup`, and the action door refuses each action with
    # 409 `needs_connection`. `*` opens every org. Empty opens none, and
    # `connections` then changes nothing, as before I1a. Reader:
    # `acb_common.studio_flags.connection_gate_on`. A SIGHUP reload applies a
    # change (docs/flag_reload.md). A flip in production is the gate
    # `enforcement-flip`, after the pre-check of ai_studio.md section 14 I1a.
    studio_connection_gate_orgs: str = ""

    # STU-B2b-2b: STUDIO_DOOR_OPS_ORGS lists the org ids where the Studio
    # action door runs a class A door op (`<app>.<op>`) of another app, as the
    # caller. `*` opens every org. Empty opens none. Reader:
    # `gateway.routes.studio.flags.door_ops_allowed`. A SIGHUP reload applies
    # a change (docs/flag_reload.md). A flip in production is the gate
    # `enforcement-flip`. Spec: ai_studio.md section 14 B2b-2.
    studio_door_ops_orgs: str = ""

    # STU-B4a: STUDIO_LOGIC_ORGS lists the org ids where the Studio action
    # door runs a first-party logic module (`logic:<name>`). `*` opens every
    # org. Empty opens none. Reader: `gateway.routes.studio.flags.
    # logic_allowed`. A SIGHUP reload applies a change (docs/flag_reload.md).
    # A flip in production is the gate `enforcement-flip`. Spec: ai_studio.md
    # section 14 B4a.
    studio_logic_orgs: str = ""

    # STU-C2c: STUDIO_NUM_F8_ORGS lists the org ids where a number filter of
    # the Studio object query adds a float8 test on `studio_record_hot.
    # v_num_f8`, so the app role can use its index (`numeric` comparisons are
    # not LEAKPROOF). `*` opens every org. Empty opens none, and the SQL is
    # then the same as before. The query honours it for an org only after the
    # backfill (`scripts/backfill_studio_num_f8.py`) ends, so the records are
    # right at every step.
    # Reader: `acb_common.studio_flags.num_f8_allowed`. A SIGHUP reload
    # applies a change (docs/flag_reload.md). Spec: ai_studio.md section 14 C2c.
    studio_num_f8_orgs: str = ""

    # STU-B1: STUDIO_ORGS lists the org ids that may read the AI Studio
    # action catalog (GET /studio/actions). `*` opens every org. Empty opens
    # none. Reader: `gateway.routes.studio.flags.studio_allowed`. Spec:
    # ai_studio.md section 3.2.
    studio_orgs: str = ""

    # STU-J10: STUDIO_RESIDENCY_ORGS lists the org ids whose health data
    # stays in India. `*` opens every org. Empty opens none (no change for
    # any org). It only adds refusals: a listed org's health app may use a
    # store or a vendor only when `acb_common.studio_residency` records the
    # region as India. NO CALLER READS IT YET: the guard `require_path` is not
    # wired into any send path, so flipping it changes nothing today.
    # Reader: `acb_common.studio_residency.residency_on`.
    # The flip is the owner's (`enforcement-flip`). Spec: ai_studio.md J10.
    studio_residency_orgs: str = ""

    # STU-C5a: STUDIO_SEARCH_ORGS lists the org ids where company search reads
    # the objects that an app names in `shell.search`, for the packages that the
    # member owns or has installed. `*` opens every org. Empty opens none, and
    # the source then runs no query. Reader:
    # `gateway.routes.studio.flags.search_allowed`. A SIGHUP reload applies a
    # change (docs/flag_reload.md). Spec: ai_studio.md section 14 C5.
    studio_search_orgs: str = ""

    tool_packs_orgs: str = ""

    # ── Waiting: what waits on you (WS-AP AP-1a) ────────────────────────────
    #
    # A comma list of organization ids, or `*` for each one. Empty means off,
    # the default. Off, `GET /shell/waiting` and `GET /shell/waiting/count`
    # answer 404. The one reader is
    # `gateway.routes.shell.waiting.waiting_allowed`. A SIGHUP reload applies
    # a change. A flip is OWNER-GATE (`enforcement-flip`).
    # Fence: tests/unit/test_waiting_seam_r8.py.
    waiting_orgs: str = ""

    # ── Web research (WS-WEB, D148, `web_research.md` §3.2 and §11) ─────────
    #
    # `web_research_orgs` lists the organization ids whose `fetch_page` reads
    # an HTML page with the local reader (Trafilatura, WEB-2), or `*` for each
    # one. Empty means off, and then `fetch_page` reads as WEB-1 did. The one
    # reader is `acb_skills.web_tools.web_research_on`, and it fails closed.
    # The tool reads it on each call. Any value on a box is a dark flag flip
    # (`work_plan.md` §6 D4).
    # Fence: tests/unit/test_web_readers.py.
    web_research_orgs: str = ""

    whatsapp_assistant_orgs: str = ""

    whatsapp_assistant_writes_orgs: str = ""

    # STU-J1-3 (ai_studio.md section 14 J1-3): the orgs where a workflow write
    # needs `feature:studio` on top of `feature:workflows`. A write is a
    # create, a duplicate, an edit, a delete, a Copilot turn or a test run of
    # the draft. `*` is every org. Empty, the default, keeps the rule before
    # J1-3. The one reader is
    # `gateway.routes.workflows.core.author_gate_on`. A flip is gate
    # `enforcement-flip`, and a SIGHUP reload applies it. Fences:
    # tests/unit/test_workflows_author_gate.py, test_workflows_author_gate_r8.py.
    workflow_studio_gate_orgs: str = ""

    # H-328 part (b): the orgs whose UNATTENDED workflow runs may start. A
    # schedule tick, a matured wait and the startup sweep act only for an org
    # in this comma list. Empty, the default, means no org, and there is no
    # `*`. The one reader is `gateway.routes.workflows.unattended`. A run can
    # send mail or WhatsApp and spend credits, so a flip is the owner's. Fence:
    # tests/unit/test_workflows_unattended_flag.py.
    workflow_unattended_orgs: str = ""
