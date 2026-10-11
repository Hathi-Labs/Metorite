# External agent dispatch — start a session on the member's own AI account

**Status: PROPOSED. Nothing is built. No decision is taken.** Written 2026-10-11.

The vendor facts come from vendor documents read on that date. A vendor fact marked **unverified** came from a search summary or from memory. An agent must read the vendor page before it builds on that fact.

**Board row:** **WS-56** (proposed).

**Companion:** `external_agent_access.md` (WS-55) owns the inbound door, where an outside agent calls Metorite. This spec owns the outbound door, where Metorite starts an outside agent.

**Registry owner:** WS-54, the Integrations app, with `crm_platform.md` §5.3 and §7.1. This spec adds no credential store. It reuses theirs.

---

## 1. The answer in one screen

A member opens a task in Projects and picks **Run with…**. The menu lists the ways to run the task. Metorite's own agent is one entry. The member's own Copilot, Jules, or GitHub-based Claude Code is another. Metorite starts the session, records it on the task, and follows it until a pull request appears.

- **Two doors make one loop.** Outbound dispatch starts the session. Inbound MCP (WS-55) lets the session read the task and report back.
- **Vendors differ, so there are four adapter kinds.** An API adapter, a forge adapter, a local runner and a hand-off. §4 explains each.
- **GitHub is the common bus.** Most coding agents already watch GitHub. One GitHub connection can start several agents.
- **A personal credential never becomes an organization credential.** The member's token runs only when that member presses Run. §5 holds the rules.
- **One registry, two scopes.** Member-scope and organization-scope connections are rows of the same table that WS-54 builds. The Integrations app shows them in two named sections.
- **Nothing here changes the work of the other sessions.** §10 lists who owns what.

## 2. What the owner asked for

1. Create a task in Projects and choose to run it with AI.
2. The run is inside Metorite when Metorite is able. Otherwise it starts a session on ChatGPT, Copilot, Gemini or another platform.
3. Those platforms need an individual login. The credentials are personal, not organizational.
4. The Integrations app separates organization integrations from the integrations that one person authorizes.

## 3. What each vendor lets a third party do

This is the fact that shapes the design. Only some vendors let an outside system start a session.

| Platform | Can Metorite start a session? | Credential | Confidence |
|---|---|---|---|
| **GitHub Copilot cloud agent** | **Yes.** `POST /agents/repos/{owner}/{repo}/tasks` with `prompt`, `base_ref`, `model` and `create_pull_request`. `GET` reads the state: queued, in_progress, completed, failed, idle, waiting_for_user, timed_out, cancelled. An issue can also go to the bot `copilot-swe-agent` through the Issues API | **User-to-server tokens only.** A personal access token, an OAuth app token or a GitHub App user-to-server token. GitHub says an installation token is not supported | Verified in [GitHub Docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api) on 2026-10-11 |
| **Google Jules** | **Yes, in alpha.** `POST /v1alpha/sessions`, then `GET .../activities`, `:sendMessage` and `:approvePlan`. The result carries pull request URLs | A key that the member creates in the Jules web app. The member must install the Jules GitHub app first | Verified in [Jules API docs](https://developers.google.com/jules/api). The spec may change |
| **Claude Code** | **No documented API for a cloud session.** An open request asks for one ([issue 97450](https://github.com/anthropics/claude-code/issues/97450), 2026-09-26). The CLI rejects `--cloud` with a task in `-p` mode. Two other ways exist: the GitHub Action, and a local run | The Action takes an API key or a long-lived subscription token from `claude setup-token`. Anthropic does not allow a third party to route requests through a member's subscription login. The press reported this policy. Read the current terms before any build | Verified for the Action and the CLI in [Claude Code docs](https://code.claude.com/docs/en/github-actions). The policy is **unverified** at the source |
| **OpenAI Codex** | **Not by a public cloud API that we found.** The SDK drives a **local** Codex through its app server and needs a signed-in account. Codex cloud tasks start from the CLI or the web | A ChatGPT sign-in, or an API key | Read in the [Codex SDK page](https://learn.chatgpt.com/docs/codex-sdk). A GitHub mention path is **unverified** |
| **Gemini CLI** | **Local only.** It has a headless mode | `GEMINI_API_KEY` | **Unverified.** From a search summary |
| **Cursor background agents** | A user API key can create and manage agents | A user API key | **Unverified.** From a search summary |
| **Devin** | A session API exists, and `create_as_user_id` makes the session belong to one person | An organization service user | **Unverified.** From a search summary |
| **ChatGPT chat, Claude.ai chat** | **No.** A chat app starts nothing. It can only **pull**, through a connector to our inbound MCP | The member signs in to Metorite through OAuth | Matches WS-55 |

**What follows from the table.**

1. Only Copilot and Jules have a clean, documented, member-credential API. Both start work on a GitHub repository.
2. A server cannot use a member's Claude subscription or Codex sign-in. Those products run on the member's own machine, or in the member's own GitHub workflow.
3. A chat product cannot be pushed. It can be offered a ready prompt, and it can pull the task through MCP.

## 4. The four adapter kinds

An **executor** is one way to run a task. Each executor uses one adapter kind.

| Kind | How it starts the work | Executors | Credential held by Metorite |
|---|---|---|---|
| **A. API** | Metorite calls the vendor API as the member | Copilot cloud agent, Jules, later Cursor and Devin | A member-scope token or key |
| **B. Forge** | Metorite acts on GitHub as the member. It assigns an issue to a bot, or posts a trigger comment. An agent that watches the repository then starts | Copilot through the Issues API, Claude Code through its GitHub Action, Codex if its mention path is confirmed | One member-scope GitHub token. One connection starts several agents |
| **C. Local** | The task waits in the member's queue. The member's own agent on the member's own machine takes it through the inbound MCP | Claude Code CLI, Codex CLI, Gemini CLI | **None.** The member's login never leaves the member's machine |
| **D. Hand-off** | Metorite prepares a prompt and a deep link. The member pastes or opens it | ChatGPT chat, Claude.ai chat, any other chat | None |
| **E. Internal** | Metorite runs the task with its own agent | Metorite agents, and the coding engine of WS-43 for code | None. It uses the organization's AI credits |

**The recommended order.** Internal first, because it exists. Then the forge adapter, because it needs one GitHub connection and reaches the most agents. Then the API adapter for Copilot and Jules. Then the local runner. The hand-off adapter is cheap and can ship at any time.

**Why a local kind exists.** It is the only way to use a member's subscription login without a server. It also needs no new code in version 1. Slice WS-55 EA-3 already lets the member's agent list the tasks that wait for it. §12 of `external_agent_access.md` walks that case.

## 5. Credential rules

These rules are the heart of the design. A rule that names a fence is enforced. A rule without a fence is advisory (work_plan.md R7).

| # | Rule | Fence |
|---|---|---|
| DX-R1 | A credential has a **scope**: `member` or `org`. The scope is a column, set at connect time, and never changes | Proposed `tests/unit/test_dispatch_credential_scope.py` |
| DX-R2 | A `member` credential is **read by one code path only**: the dispatcher, acting for that member, at the moment that member presses Run or sets a standing rule | Same test, with a structural scan that no other module reads the blob |
| DX-R3 | No other member, no organization admin, and no agent acting for the organization can **use** it. An admin can see that it exists, its health and its last use. An admin can **revoke** it. An admin cannot read it | Same test |
| DX-R4 | A task that belongs to the organization may run on a member's account only if that member started the run. The run record names the member and the executor | The run table of §6 |
| DX-R5 | The compute and the cost fall on the member's own account (Copilot premium requests, the Jules quota). The task timeline says so | Advisory |
| DX-R6 | The connect flow is **one click and a consent screen** wherever the vendor offers OAuth. Metorite owns the OAuth application. A member pastes a key only where the vendor offers nothing else, and the Jules key is that case today | Advisory. Customer setup is one click |
| DX-R7 | When a member leaves the organization, Metorite revokes every `member` credential of that member and marks each run in flight as `orphaned` | Proposed `tests/unit/test_dispatch_offboarding.py` |
| DX-R8 | A token that Metorite holds is encrypted at rest through the existing store. It is never logged, never returned by an API, and never placed in a prompt | The existing `key_store` fences, and a log scan |
| DX-R9 | The organization decides **which executors members may use**. The default is off for every external executor. An admin switches each one on | Proposed `tests/unit/test_dispatch_policy.py` |

**Where the credential lives.** In `integration_connections` (WS-54, `crm_platform.md` §5.3). That table already has `scope_kind` (`org` or `member`), `member_email`, `credentials_encrypted` and `connected_by`. A new executor is a new `provider` value. This spec adds **no table for credentials**.

**What this spec needs from that table.** Section 10 lists four requests. The WS-54 session decides each one.

## 6. The run record

Assignment is already dispatch for Metorite's own agents. `agent_dispatch.py` starts an internal run when a task gets an `agent:<name>` assignee. It is dark behind `PROJECTS_AGENT_DISPATCH`, and `project_management_app.md` §9.12.10 keeps "Assign to AI" parked. D-PM-39 says only the server may create an agent row.

An external run is **not** an assignee. A person owns the work, and the agent is a tool. The proposal is one table.

`pm_agent_runs`

| Column | Meaning |
|---|---|
| `id`, `organization_id`, `task_id` | Tenant-scoped, as R5 requires |
| `executor` | `internal`, `copilot_cloud`, `jules`, `github_claude`, `local`, `handoff` |
| `member_email` | Whose credential and whose request. Set by the server, never by the client |
| `connection_id` | The `integration_connections` row that was used. Null for internal, local and hand-off |
| `external_ref`, `external_url` | The vendor session id and its web address |
| `state` | `queued`, `running`, `waiting_for_user`, `needs_review`, `done`, `failed`, `cancelled`, `orphaned` |
| `idempotency_key` | One run per task and executor and request, so a double click starts one session |
| `last_polled_at`, `last_error` | Health |
| `created_at`, `finished_at` | Lifecycle |

**State mapping.** Each adapter maps the vendor state to this set.

The Copilot map is the example. Copilot `queued` stays `queued`. `in_progress` becomes `running`. `waiting_for_user` stays.

`completed` becomes `needs_review` when a pull request exists. `failed` and `timed_out` become `failed`. `cancelled` stays `cancelled`.

**The timeline.** A run writes `agent_run` activities, a type that already exists. The first row lands before the vendor call returns, so the member sees the handoff at once. The row text names the executor, the member and the cost owner.

**Pull requests.** When a run reports a pull request, the dispatcher calls the same write path as the inbound tool `add_task_reference` (`pm_task_refs`, WS-55 §11). One table holds every reference, whoever made it.

## 7. The Projects experience

**Run with…** is a menu on the task, in the task detail and the row menu. It lists only what the member may use.

1. **Metorite.** Shown when the task type and the organization allow it. The coding engine (WS-43) serves code tasks. Other tasks go to the assistant.
2. **My connected agents.** One entry for each executor that the organization allows and the member has connected. An entry that needs a connection shows "Connect" and opens the consent flow.
3. **Copy for another agent.** The hand-off. It copies a prepared prompt and opens a link.

After the member picks one, the task shows a run card. The card holds the executor, the state and a link to the vendor session. It has a Cancel button where the vendor allows it. A pull request appears on the task as a reference.

**The project holds the repository.** Adapters A and B need a repository and a base branch. This spec adds one project setting, `repo` and `base_ref`, owned by the Projects settings pane (`projects_settings.md`). A task with no repository offers only Metorite and the hand-off.

**What the member never does.** The member never pastes a task into a vendor by hand for kinds A, B and E.

## 8. The prompt and the report-back

**The prompt envelope.** The dispatcher builds one prompt from the task: the title, the description, the acceptance text, the repository, the base branch and the task key. It adds one line that names the task key so a commit or a pull request carries it. The text of the task is untrusted input to the vendor agent. WS-55 §9.4 applies.

**How the session reports back. Two modes.**

| Mode | How | Trust cost |
|---|---|---|
| **Observe** (version 1) | Metorite polls the vendor API and reads GitHub. It sees the state and the pull request. The session gets no Metorite credential | None. Recommended |
| **Report** (later) | The session calls Metorite's inbound MCP with a **run-scoped token**. The token is limited to one task, allows `get_task`, `add_comment`, `add_task_reference` and `set_task_status`, and expires with the run | A secret reaches a vendor sandbox. Only for a vendor that has a secrets channel |

A run-scoped token is a WS-55 connection with a `task` pin and a short life. It needs one new field on `agent_connection`. Do not hand a token over in the prompt text, because a vendor logs prompts.

## 9. Weaving it together

```
          member
            │  Run with…            (Projects task)
            ▼
     dispatcher  ── policy: executor allowed for this org?
            │     ── credential: member-scope row, this member only
            ├────────────► E. Metorite agent / coding engine
            ├────────────► A. vendor API  (Copilot cloud, Jules)
            ├────────────► B. GitHub: assign issue / trigger comment
            ├────────────► C. queue for the member's local agent
            └────────────► D. prompt + link for a chat app
                                   │
   external session  ◄─────────────┘
        │  reads task, reports  (WS-55 inbound MCP, member's own connection
        ▼                          or a run-scoped token)
     Metorite: pm_agent_runs, timeline, pm_task_refs
```

**One member, one set of connections.** The Integrations app lists what the member has authorized, in both directions:

- A service Metorite uses for the member (GitHub, Jules, Copilot).
- An agent that uses Metorite as the member (a WS-55 connection).

**Shared vocabulary.** Both directions use the same words in the product: **connection**, **scope**, **owner**, **health** and **revoke**.

## 10. The Integrations app and the other sessions

Two other sessions own parts of this ground. This spec does not touch their code. It makes four requests and offers one section.

| Session | Owns | This spec asks |
|---|---|---|
| **CRM, WS-53** | The CRM app, the Zoho mirror, `integration_connections` in its first form (`crm_platform.md` §5.3) | Keep `scope_kind` and `member_email` general. Keep one store module as the **only** reader of the blob. Do not hard-code CRM names in the table |
| **Integrations app, WS-54** | The admin console over the registry, the listing route, the page | (1) Show **two sections**: **Organization** and **My integrations**. (2) Add a `direction` field or derive it, so inbound and outbound share one list. (3) Make a `member` row visible to its owner and to an admin as metadata only (DX-R3). (4) Offer a generic OAuth connect helper that a new provider can reuse |
| **This spec, WS-55 and WS-56** | The inbound server and the dispatcher | Build the dispatcher on the registry after WS-54's registry slice lands. Until then, build only what needs no registry |

**The two sections the owner asked for.**

| Section | Who sees it | Holds |
|---|---|---|
| **Organization** | Admins act. Members see the status | Shared services (Zoho, a shared mailbox), the executor policy of DX-R9, the client allow list of WS-55, the audit view |
| **My integrations** | Each member sees their own | The member's GitHub, Copilot, Jules and other personal accounts, and the member's inbound agents, with Connect, Reconnect and Revoke |

An admin sees a member's row as metadata: the provider, the owner, the health and the last use. The admin gets a Revoke button and never a "view token" button.

**Reach.** `external_agent_access.md` §5.4 already notes that the app sits in the Admin section today, so a member cannot reach it. WS-54 must show "My integrations" to every member.

## 11. Decisions to take (proposed D-DX-1 to D-DX-8)

The owner decides these. Each line has a recommendation that a build may use after the owner accepts it.

| # | Question | Recommendation | Why |
|---|---|---|---|
| D-DX-1 | Which executor first | Metorite internal, then the GitHub forge adapter | One GitHub connection reaches Copilot and Claude Code. The internal path already exists |
| D-DX-2 | How a member connects GitHub | Metorite owns a GitHub App and uses its **user-to-server** OAuth flow. No pasted personal access token | GitHub's agent API refuses installation tokens. A user-to-server token is one click and expires |
| D-DX-3 | Jules key | A pasted key, with a clear note, until Google offers OAuth | The vendor offers nothing else |
| D-DX-4 | Claude Code and Codex on a server | Not in version 1. Offer the local kind and the forge kind | A member's subscription login must not run on our server |
| D-DX-5 | Default for each external executor | Off for every organization. An admin switches each on | The task text leaves Metorite. The H-236 rule treats an outside call as egress |
| D-DX-6 | Report-back | Observe in version 1. Add run-scoped tokens later | Handing a vendor sandbox a secret needs its own review |
| D-DX-7 | Who pays | The member's own account for an external run. Metorite credits only for internal runs | It follows from the credential rule and needs no billing code |
| D-DX-8 | Where the repository mapping lives | A project setting, `repo` and `base_ref` | Tasks inherit it, and the settings pane already exists |

## 12. Slices (every slice ships dark)

A slice is **AGENT-SAFE** unless it says **OWNER-GATE**.

| # | Slice | Done when | Gate |
|---|---|---|---|
| DX-0 | Owner decisions D-DX-1 to D-DX-8, and a board row | The answers are in §11 and the row is on the board | OWNER-GATE |
| DX-1 | `pm_agent_runs`, the state set, the timeline rows, the idempotency key, and the internal executor wrapper over `agent_dispatch.py` and WS-43 | A real-database test shows one run for a double click, and a state walk to `done` | AGENT-SAFE |
| DX-2 | **Run with…** menu and run card in Projects, behind a flag | The menu lists only allowed executors. The card shows the state | AGENT-SAFE |
| DX-3 | Executor policy in the Integrations app (organization section) | A test shows an executor that is off never starts | AGENT-SAFE, after WS-54 |
| DX-4 | Project `repo` and `base_ref` setting | A task inherits the value, and a task with no repository offers no external executor | AGENT-SAFE |
| DX-5 | GitHub connection: the GitHub App, the user-to-server flow, the member-scope row | A member connects once. A structural test shows only the dispatcher reads the blob (DX-R2) | AGENT-SAFE, then **OWNER-GATE** for the GitHub App registration and its secrets |
| DX-6 | Forge adapter: Copilot through the Issues API, Claude Code through its Action | A run starts from a task, and the pull request lands as a reference | AGENT-SAFE |
| DX-7 | API adapter: Copilot cloud agent tasks API and Jules | The state map of §6 holds for both. A vendor failure shows on the card | AGENT-SAFE |
| DX-8 | Hand-off adapter: prompt and deep link | The copied prompt holds no secret and no id that the member cannot see | AGENT-SAFE |
| DX-9 | Offboarding: revoke and `orphaned` (DX-R7) | A removed member's connections are revoked and the runs show `orphaned` | AGENT-SAFE |
| DX-10 | Run-scoped tokens on WS-55's connection | A token works for one task and dies with the run | AGENT-SAFE, after D-DX-6 changes |
| DX-11 | Local runner, optional | A daemon on the member's machine takes tasks through the inbound MCP | AGENT-SAFE, later |

**Order.** DX-0, then DX-1, DX-2 and DX-4. Then DX-3 and DX-5 after WS-54's registry slice. Then DX-6 to DX-9.

## 13. What can start now, and what must wait

| Work | Depends on | Can start now |
|---|---|---|
| WS-55 EA-1 to EA-3, the inbound server | Nothing in WS-53 or WS-54 | **Yes** |
| DX-1, the run table and the internal wrapper | `PROJECTS_AGENT_DISPATCH` and WS-43 | After DX-0 |
| DX-5 to DX-9, the credentialed adapters | WS-54's `integration_connections` slice | **No.** Wait for WS-54 |
| The two Integrations sections | WS-54's page | **No.** Wait for WS-54 |

## 14. Risks

- **Vendor drift.** Jules is alpha. Copilot's agent API is new. Keep each adapter behind one interface and one fence that checks the state map.
- **Data egress.** The task text and the repository reach a third party. The organization switch (DX-R9) and the consent text carry this. A legal review belongs to WS-37.
- **A runaway cost on a member's account.** The member pays and may not expect it. The first run for each executor shows a one-time notice.
- **Terms of service.** Do not run a member's Claude or ChatGPT subscription login on a server. Read each vendor's current terms before a build, because the facts in §3 move.
- **Loop risk.** An agent that updates a task may trigger another dispatch. A run for the same task and executor stays blocked while one is active (the idempotency key).
- **GitHub App scope.** A broad App permission set is a risk of its own. Request only the permissions that the two flows need.

## 15. Changelog

- 2026-10-11 — First draft. Research record, the four adapter kinds, the credential rules and the slices. No code.
