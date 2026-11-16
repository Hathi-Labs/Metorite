# Tool failure log: one failure store, one fingerprint, one task for each cause

<!-- ste-tier: strict -->

**Status: PROPOSED, 2026-11-16. Verified against the code on 2026-11-16 at
`origin/main` `20ef30cfb`. Audit rounds 1 and 2, the delta audit, the
changed-lines audit and the final changed-lines audit are applied,
2026-11-16. A last check of the R9 sentences is pending.** Nothing is
built. The owner activates this spec after an Opus spec audit gives GO.

No build slice starts before the owner activates this spec. The defaults of
§15 hold until the owner answers. TOF-Q2 is the exception: TOF-2b waits for
the owner's answer.

**Merge after PR 2445.** This spec cites D225, which only the open PR 2445
holds. Merge this spec after PR 2445. At merge, re-check D225 here, in
`INDEX.md`, and in the WS-IR and WS-TOF rows of `work_plan.md`.

- **Board.** TOF-2a is task 3416 (this spec). Epic 3393 (WS-TOF) holds the
  slices. Task 1793 is the build umbrella. Task 3417 is the rung 2 demo. SBX-14
  (task 3429) waits for this spec.
- **Decisions it builds.** D225 points 4 and 5 (PR 2445). The owner approved
  them on task 3394 on 2026-11-15. They give one store, one fingerprint, and
  the consent rules of D126 and SBX-Q8. D225 does not fix the store name or
  the fingerprint fields. This spec proposes both.
- **Reads.** D12, D15, D64, D126, D136.2, D140.2, D187, D205, D211, D224,
  D225, R1 and R5 to R8.
- **Owns.** The failure store, the fingerprint, the one write seam, and the
  one task for each fingerprint. Every other doc that mentions this work links
  here and adds nothing.
- **Does not own.** The issue report form and the chat bundle (IR-0, task
  555). The repair of a built-in tool (`agent_sandbox.md` §6.7, SBX-14). The
  text of a chat failure (`chat_failure_modes.md`, HRD-1 and HRD-3). The task
  store and GH-6 (`project_management_app.md` §12, `github_integration.md`
  §12). The MCP client and its gate (`agent_architecture.md` §12.2). The
  incidents of unattended jobs (`proactive_agent.md`, AUTO-2).

**Ids.** The slices are TOF-2a to TOF-2i (R2). The owner questions are
TOF-Q1 to TOF-Q4. Each fence is a named test file.

## 0. The answer in one screen

1. **One store.** A failed tool call writes one row in its own tenant, and
   adds one count to a platform row. Issue reports (IR-4) use the same store.
2. **One fingerprint, with no message in it.** The fingerprint is a hash of
   four fields. They are the platform tool name, a failure code, the
   exception type and the code site. Each field comes from code or from a
   closed list. The error message never enters the fingerprint (§3.3).
3. **Only counts and code facts cross a tenant.** §10.1 lists each field.
   No platform row holds an organization id or tenant text. The owner
   confirms the list before TOF-2b starts (TOF-Q2).
4. **One task for each fingerprint.** A platform job writes it in the
   internal project "Tool failures". A repeat updates the counts. The job
   alone closes the task, 7 days after the fix is live with no repeat. A
   repeat after the fix reopens the same task.
5. **Error text only with consent, one report at a time.** A card shows the
   exact payload, and the member sends it or not (D126, SBX-Q8). No standing
   setting exists. The card and the read are narrowed out for now (§8).

## 1. Scope and non-goals

**In scope:** the fingerprint (§3), capture from three sources (§4), the
store (§5), the write seam (§6) and the task seam (§7). The consent card and
the audited read are designed in §8, and narrowed out of dispatch.

**Non-goals:**

- **No capture outside the pack tools in the chat.** C1 sees pack tools only.
  `pack_tool_guard.guard` has one caller (`_tool_injection.py:3804`). The
  core tools and the delegated agents have no capture site yet. A later slice
  adds them.
- **No tenant view.** A member or an admin gets no page of failures in this
  spec. LOOP-16 may read a member's own rows as counts (§14).
- **No new MCP tool.** A coding agent reads the task through the tools that
  exist (`get_task`, `search_tasks`). D136.2 keeps the MCP at its tools. A
  report with text never reaches the MCP (§8.2).
- **No error text by default.** No row holds an argument, a result or an
  exception message, except a report that a member sends (§8).
- **No automatic fix.** A coding agent fixes through a pull request and the
  D145 loop. Nothing changes platform code at run time (D187.3).
- **No browser capture.** IR-1 owns errors in the page.

## 2. The code at `20ef30cfb`

Re-verify each anchor at dispatch. Line numbers move.

| Seam | Anchor | What it does today |
|---|---|---|
| The pack tool guard (HRD-3) | `packages/acb_skills/acb_skills/pack_tool_guard.py`, `guard` lines 374 to 402, `failure_text` line 132 | Turns an exception into "The tool failed (KeyError)". The log line holds the tool and the class only |
| The pack call wrapper | `apps/services/orchestrator/orchestrator/_tool_injection.py`, `_pack_call` line 3696, the one `guard` call at line 3804 | Puts the HRD-3 guard on the outside of every pack tool |
| The CRM read tools and the package | `packages/acb_skills/acb_skills/crm_tools.py`, `_read_tool`, its wrap at line 591. `packages/acb_skills/acb_skills/__init__.py`. `executor.py` line 45 | `refusals_as_text` wraps each injected CRM read tool, and no guard does. The orchestrator imports `acb_skills` at start |
| The pack owners | `_tool_injection.py` lines 3365 to 3378 | Loads `apps/agents/<owner>/agents.py` as the module `_tool_pack_owner_<owner>` |
| A Projects pack tool | `apps/skills/skill-projects/skill_projects/writes.py`, `create_task` line 1024 | A pack tool under `apps/skills` |
| The unknown tool answer (HRD-1) | `packages/acb_skills/acb_skills/unknown_tool.py`, `UnknownToolAnswer._rewrite` lines 265 to 296 | Rewrites the MAF text `Requested function "x" not found`. No function middleware runs for such a call |
| The gateway refusal text | `packages/acb_common/acb_common/tool_refusals.py`, `refusals_as_text` lines 404 to 437, `GatewayRefusal` line 92 | Turns a refused gateway call into "Refused:" text for Projects, CRM and email |
| Status errors that are not `GatewayRefusal` | `apps/skills/skill-my-tasks/skill_my_tasks/core.py` `_GatewayError` line 181. `apps/agents/agent-crm/agents.py` `_MirrorBlocked` line 209 (a 409) | A gateway status as a plain `RuntimeError` |
| The chat MCP gate | `apps/services/orchestrator/orchestrator/_maf_mcp.py`, `_McpPermissionGate.process` line 1406, `call_next` at lines 1448 and 1470 | Gates each MCP function call of a chat run |
| The chat MCP connect | `_maf_mcp.py`, `RunScopedMCPTool.__aenter__` line 684, `_reason` line 236 | Logs `mcp.unreachable` with the class of the deepest error |
| An MCP `isError` answer | `.venv/.../agent_framework/_mcp.py` lines 2848 to 2858 | MAF raises `ToolExecutionException` with the server's text |
| The Studio MCP read | `apps/services/gateway/gateway/connections/mcp_tools.py`, `_list_stored` line 438, `_live` line 1050, `REASON_*` lines 145 to 148 and 645 | Logs `studio.mcp_tools.failed` and `call_failed` with a reason code |
| The tenant scope of a request | `apps/services/gateway/gateway/main.py`, `TenantScopeMiddleware` lines 1236 to 1248 | Binds and releases the tenant of each request. An unhandled exception passes through it |
| The 500 handler | `main.py`, `_db_unavailable` lines 1185 to 1204 | Answers 503 for a refused database, else a bare 500. It runs after the tenant scope ends |
| A 5xx that a route answers | `routes/agent.py` line 4725 (500) and line 1640 (502), among about 220 sites | The inner `ExceptionMiddleware` answers it, so no exception reaches the tenant scope |
| The agent edge | `apps/services/agent_edge/agent_edge/tools.py`, `_door_body` line 10437 | Maps a 5xx to `TEXT_BUSY`. The edge has no database seam, so it captures nothing |
| The run context | `apps/services/orchestrator/orchestrator/executor.py`, `bind_artifact_context` at lines 6200 and 7542 | Binds the run, the agent and the thread. It binds no channel key |
| The release | `apps/services/gateway/gateway/build_info.py`, `build_sha` line 112, cached at start | The SHA of the running gateway. The module runs no subprocess |
| The one task creator | `apps/services/gateway/gateway/routes/projects/tasks.py`, `check_task_values` line 1217, `create_task_in` line 1260 | Makes a `pm_tasks` row for a member |
| A system move of a task | `apps/services/gateway/gateway/routes/projects/github_merge.py`, `_rule_for_task` line 215. `core.py` `apply_status_transition` line 3757 | GH-6 moves an agent-worked task to done at a merge, as `system:github` |
| The task source values | `routes/projects/core.py`, `TASK_SOURCES` line 427 | Holds `automation`, so no new CHECK value is necessary |
| The tenant seam | `packages/acb_common/acb_common/db.py`, `tenant_session` line 510, `current_tenant` line 504 | The one session idiom (R5 d) |
| The Redis prefix wrapper | `packages/acb_common/acb_common/tenant_redis.py` | A key with no tenant cannot be built (R5 c) |
| The secret shapes | `packages/acb_common/acb_common/secret_shapes.py`, `REDACTION_SHAPES` line 69 | The scrub list of D126.2 |
| The exempt list | `scripts/gen_tenant_migration.py`, `EXEMPT` line 70 | The security review of each cross-tenant table |
| The precedents | `infra/postgres/222_chat_session_exists.sql`, `276_email_detached_purges.sql` | A DEFINER function that answers across tenants. A table that only DEFINER functions reach |
| The job incident ledger | `apps/services/gateway/gateway/jobs/unattended.py`, `record_failure` line 323. `unattended_incidents` (migration 313) | One open incident for each job and error class (AUTO-2), dark |
| The one recurring seam | `docs/job_queue.md` §5c, `worker.seed_hooks`. `gateway/main.py` lines 520 to 556 import the seed consumers | A consumer adds a seed hook, and starts no second loop |
| The job registry | `apps/services/gateway/gateway/jobs/worker.py`, `register` line 193, `EFFECTS` line 151. `worker.prune_hooks` (`docs/job_queue.md` §5b) | A `write` kind names a policy. A prune hook runs in the session of each org |
| The Studio live read | `routes/studio/invoke.py` lines 2438 to 2445, raised at line 2721 | Turns a `ListFailed` of `_live` into an `HTTPException` 502 |
| The core tools | `apps/services/orchestrator/orchestrator/agents.py` lines 1170 to 1182 | The Knowledge tools are core tools. `pack_tool_guard` never wraps them |
| The task status categories | `routes/projects/core.py` `STATUS_CATEGORIES` line 339 | `backlog`, `todo`, `in_progress`, `done`, `cancelled`, `triage`. No category names review |

**What is not there.** No tool failure store, no fingerprint and no "Tool
failures" project exist. The chat history keeps each tool result in
`chat_message.tool_events`, and `agent_run` keeps the folded trace. This spec
points to them by id and copies neither. The job incident ledger stays a
separate store for now (§14.2).

**Verify at dispatch.** The code does not prove these five facts. The slice
that needs a fact proves it first, or stops and asks.

1. The tenant is still bound inside `TenantScopeMiddleware` when a route
   raises (TOF-2d, with a test).
2. `GATEWAY_URL` on production names the gateway on the box, with no proxy
   between (TOF-2c).
3. `JOB_QUEUE` is on or off on production. If it is off, TOF-2f asks the
   owner to flip it.
4. The gateway unit may run `git` under its systemd sandbox (TOF-2e).
5. The project Observability & Alerts can hold a child project (TOF-2b).

## 3. The fingerprint

### 3.1 The fields

The fingerprint is the first 16 hex characters of a SHA-256 over one line:

```text
v1|<tool>|<error_code>|<exc_type>|<code_site>
```

| Field | What it holds | Where it comes from |
|---|---|---|
| `v1` | The version of the rules | A constant. A rule change makes a new version, on purpose |
| `tool` | The platform name of the tool | §3.2 rule 1 |
| `error_code` | One code from the closed list of §3.2 rule 2 | The capture site |
| `exc_type` | `module.QualName` of the deepest exception, or `-` or `other` | §3.2 rule 3 |
| `code_site` | `module:function` of the deepest frame in our code, or `-` | §3.2 rule 4 |

The source, the surface, the release and the tenant are columns, never inputs
of the hash. So a door 5xx and an issue report of the same fault share one
fingerprint, and one release does not split a cause.

### 3.2 The normaliser rules

One module holds the rules:
`packages/acb_common/acb_common/failure_fingerprint.py` (new, TOF-2b). It is a
pure function. It reads no message, no argument, no result, no tenant and no
member.

1. **The tool.** Admit a name only when a platform registry holds it.
   - A chat tool: the name, when `builtin_tool_names()` holds it. A new
     module, `acb_skills/builtin_tools.py` (TOF-2b), holds the function. It
     does not edit `acb_skills/tool_packs.py`, which the session cal-tasks
     holds. The set never holds an F3 skill pack, because a skill name is
     tenant text. Any other name gives `other`.
   - `acb_skills.builtin_tools` gives the set to
     `failure_log.set_builtin_names()` once, at import. `record_tool_failure`
     passes it to the normaliser, so no site passes it. With no set, a chat
     name gives `other`.
   - The set comes from the tool names that `acb_skills.skill_families`
     declares. That module imports the standard library only. So
     `builtin_tools` imports `skill_families` and `acb_common.failure_log`,
     and no cycle can form.
   - `pack_tool_guard.py` and `unknown_tool.py` import `builtin_tools` at
     their top. The guard wraps every pack tool, so the set is in place before
     C1 or C2 runs.
   - C4 also runs in the injected CRM read tools (`crm_tools._read_tool`),
     which no guard wraps. So `acb_skills/__init__.py` also imports
     `builtin_tools`. The orchestrator imports `acb_skills` at the top of
     `executor.py` (line 45), so the set is in place at start.
   - The fence: `test_failure_fingerprint.py` asserts that
     `failure_fingerprint.py` and `failure_log.py` import no `acb_skills`
     module.
   - A Studio skill or action: `studio`. An MCP server and its tools: `mcp`.
     A sandbox user tool: `user_tool`. Each name of these is tenant text.
   - A door 5xx: `route:<METHOD> <path>`. The path is `scope["route"].path`.
     It counts only when it equals the `.path` of a route in `app.routes`,
     read once at start. Any other path gives `route:other`. So a concrete
     path with an id never passes.
   - The name that `unknown_tool._RAW` reads is model text. It counts only
     when `builtin_tool_names()` holds it.
   - The grammar of the column: a built-in name matches
     `^[a-z][a-z0-9_]{0,79}$`. The other values are `other`, `studio`, `mcp`,
     `user_tool`, and a `route:` value as above.
2. **The failure code.** One value of this closed list. A code marked "-"
   writes `-` in `exc_type` and `code_site`, because the code holds the
   meaning. So an outage of one outside system gives one fingerprint.

   | Code | Meaning | Owned by us | Type and site |
   |---|---|---|---|
   | `exception` | A tool raised | Yes | Kept |
   | `bad_arguments` | HRD-3 refused an argument shape | Yes | - |
   | `unknown_tool` | The model called a tool that the run did not load | Yes | - |
   | `auth_401` | Our service call got 401 from the gateway | Yes | - |
   | `unhandled_500` | A gateway route raised | Yes | Kept |
   | `route_500` | A gateway route answered 500 on purpose | Yes | - |
   | `route_upstream` | A gateway route answered 502 or 504 on purpose | No | - |
   | `gateway_unreachable` | A tool could not reach the gateway | No | - |
   | `outside_unreachable` | A pack tool could not reach an outside host, such as Google | No | - |
   | `mcp_exception` | Our own MCP client code raised (§4.3) | Yes | Kept |
   | `mcp_timeout`, `mcp_unreachable`, `mcp_refused`, `mcp_too_large`, `mcp_tool_error` | An outside MCP server failed, or our guard refused it (§4.3) | No | - |

3. **The exception type.** Walk the causes and the groups to the deepest
   exception. This walk does not follow `__context__`, unlike
   `_maf_mcp._reason`. Rule 4 uses the same walk. Keep `type.__module__` and
   `type.__qualname__` only when all three hold:
   - the root of the module is in the allow list: `sys.stdlib_module_names`,
     our roots (`acb_*`, `gateway`, `orchestrator`, `agent_edge`,
     `email_ingestion`, `ingestion`, `skill_*`, `_tool_pack_owner_*`), and the
     pinned libraries (`httpx`, `httpcore`, `anyio`, `sqlalchemy`, `asyncpg`,
     `psycopg`, `pydantic`, `pydantic_core`, `agent_framework`, `mcp`,
     `openai`, `redis`, `starlette`, `fastapi`),
   - the name matches `^[A-Za-z_][A-Za-z0-9_.]{0,120}$`,
   - an attribute walk of the qualname from `sys.modules[module]` gives the
     same class, `type(exc)`.

   Else the type is `other`. A capture with no exception gives `-`.
4. **The code site.** Walk the exceptions from the deepest one out to the
   one that the capture site caught. In each, walk its traceback from the
   deepest frame up. The first frame whose file is in one of four trees wins:
   `apps/services/*/`, `apps/skills/*/`, `apps/agents/*/` and `packages/*/`.
   A file under `site-packages` never counts.
   - The module is `frame.f_globals["__name__"]`. So the CRM pack owner (owner
     `crm-assistant`, directory `apps/agents/agent-crm`) reads as
     `_tool_pack_owner_crm_assistant`. The function is the frame's code name.
   - A function name that does not match `^[A-Za-z_][A-Za-z0-9_]{0,80}$`
     gives `module` or `lambda`. No line number goes in, so a release that
     moves lines keeps the fingerprint.
   - The trees sit under one root. The normaliser takes `repo_root`, and
     `None` means the repository root, `Path(__file__).resolve().parents[3]`
     of `failure_fingerprint.py`. A test passes a temporary root.
   - No such frame gives `-`. The column grammar is
     `^[A-Za-z_][A-Za-z0-9_.]{0,150}:[A-Za-z_][A-Za-z0-9_]{0,80}$`, or `-`.
5. **Never an input.** `str(exc)`, `exc.args`, arguments, results, headers,
   the tenant, the member, the server name, the release and the time.

### 3.3 Why the message stays out

The brief of TOF-2a (task 3416) gave two choices, and D225 decides neither.
One is a proof that a normaliser removes all tenant text. The other keeps the
message out. **This spec keeps the message out.**

- **No normaliser can give that proof.** "Amount for Ravi Kumar" holds a name
  in plain words. Only a list of allowed words could catch it, and that list
  would remove the message too.
- **The code site does the work of the message.** Two faults at one site with
  one type are, almost always, one cause. A coding agent reads the site first.
- **The code agrees today.** `pack_tool_guard.failure_text` shows the class
  only. `safe_detail` drops each 5xx detail. `agent_sandbox.md` §7.3 plans
  `user_tool_runs.error_class` as a fixed list too, though nobody built it.
- **The cost.** Two causes at one site with one type share a task. The coding
  agent splits them in the fix, and a consented report (§8) carries the text.

### 3.4 Worked examples

`tool_failure_log_examples.md` holds 12 golden rows, each with its real hash.
Two of them show the main rule. `KeyError("Amount for Ravi Kumar")` in org A
and `KeyError("Amount for Acme Pvt Ltd")` in org B, both in `crm_get_record`,
give one fingerprint, `00bc2542a73b3967`. A timeout of the server
"shaurrya-app" and one of the server "acme-erp" give one fingerprint too.
TOF-2b uses the rows as golden cases in `test_failure_fingerprint.py`.

## 4. The capture matrix

### 4.1 What counts as a failure

A refusal is an answer, not a failure (`agent_sandbox.md` §6.7). The capture
sites skip these: 400, 403 (a declined card too), 404, 409, 412, 422, 429,
and every 503. A 503 is the gateway's busy answer (`db_busy`,
`identity_unavailable`, `provider_did_not_answer`). The sites also skip a
timeout of the whole run, and a run that the member stops.

### 4.2 Which layer writes

Each failure has one writer. A gateway 5xx is written at the gateway, by D1
or D2, never in the caller.

| # | Source | Surface | Capture site | `error_code` |
|---|---|---|---|---|
| C1 | `chat_tool` | the run's channel | `pack_tool_guard.guard`, the `except Exception` branch, with the skip rule below | `exception`, `auth_401`, `gateway_unreachable`, `outside_unreachable` |
| C2 | `chat_tool` | the run's channel | `pack_tool_guard.guard`, the `take_bad` branch | `bad_arguments` |
| C3 | `chat_tool` | the run's channel | `UnknownToolAnswer._rewrite`, once for each call of this run. A result from the chat history does not count again | `unknown_tool` |
| C4 | `chat_tool` | the run's channel | `refusals_as_text`, for a gateway 401 only | `auth_401` |
| C5 | `mcp_call` | the run's channel | `_McpPermissionGate.process`, around each `call_next()`, by the map of §4.3 | an `mcp_*` code |
| C6 | `mcp_call` | the run's channel | `RunScopedMCPTool.__aenter__`, the `except Exception` branch, by the map of §4.3 | an `mcp_*` code |
| C7 | `mcp_call` | `studio` | `mcp_tools._list_stored` and `_live`, the `except Exception` branch, by the map of §4.3, as C5 does. C7 also records `mcp_tool_error` around `shape_result` in `_live`, which raises `ListFailed(REASON_TOOL_ERROR)` after that `try`. `cache_unavailable` is our Redis, so it does not count | an `mcp_*` code |
| D1 | `door_5xx` | from the `X-Actor-Via` prefix | `TenantScopeMiddleware.__call__`, an `except Exception` before the `finally` that releases the tenant. It records, then raises again | `unhandled_500` |
| D2 | `door_5xx` | from the `X-Actor-Via` prefix | `TenantScopeMiddleware.__call__` wraps `send`, and reads the status of `http.response.start`. It counts only a response that no exception of D1 passed | `route_500`, `route_upstream` |

**One writer for each request.** `record_tool_failure` marks the request
scope. D2 skips the response of a marked request. So a Studio live read that
times out writes one `mcp_timeout` at C7. The 502 that `routes/studio/invoke.py`
then answers writes no `route_upstream`.

**The mark.** `TenantScopeMiddleware` puts a mutable holder in a `ContextVar`
before it calls the app. `record_tool_failure` sets a flag on that holder. A
`ContextVar.set()` lower down would not reach the middleware from a child task
or the thread pool, such as the `ensure_future` of `_pinned`. A shared holder
does. The mark stops D2 only, and D1 still records an exception.

**A 5xx that nobody writes.** D2 counts 500, 502 and 504 only. A route that
answers 501, 505 or another 5xx writes nothing, on purpose. Such a status
says "not built" or names a protocol, and it is not a fault.

**The C1 skip rule.** C1 reads one number from the chain of §4.3: the
causes, the contexts and the groups. It reads no text. The type and the site
still come from §3.2 rules 3 and 4. A status is the gateway's only in two
cases:
- `.status` of a `GatewayRefusal`,
- `.response.status_code` of an `httpx.HTTPStatusError`, when its
  `request.url.host` equals the host of `GATEWAY_URL`.

Any other status is an answer of an outside host, such as Google or GitHub,
so C1 skips it. For a status of the gateway:
- a status of §4.1 is an answer, so C1 skips it,
- a 401 gets `auth_401`,
- a 500 or more was written by D1 or D2, so C1 skips it.

An `httpx.TransportError` in that chain whose request host is the gateway
host gets `gateway_unreachable`. One to any other host gets `outside_unreachable`, which
we do not own. The tools call `GATEWAY_URL` on the box, with no proxy between.
So each 5xx status that a tool reads came from a gateway route.

**Status errors to convert.** Two pack transports raise a gateway status as
a plain class: `skill_my_tasks` `_GatewayError` (`core.py` line 181) and
agent-crm `_MirrorBlocked` (`agents.py` line 209, a 409). TOF-2c makes each a
subclass of `GatewayRefusal` that keeps its status. The fence lists each pack
transport.

**The Knowledge tools are out of scope.** They are core tools
(`orchestrator/agents.py` lines 1170 to 1182), so `pack_tool_guard` never
wraps them, and C1 never sees them. Their plain raises in `knowledge_tools.py`
(lines 213, 224, 237, 256 and 480) stay as they are. The later slice of core
tools (§1) handles them.

**What "door 5xx" means here.** D1 and D2 count each gateway route that
fails, whoever called it: a chat tool, the MCP edge, a workflow or a browser.
A route that fails is a fault in our code for every caller.

**Why D1 sits in the middleware.** `_db_unavailable` runs in the outer
`ServerErrorMiddleware`, after `release_tenant`. So the tenant is gone there.
D1 skips an exception that `db_busy.is_db_unavailable` names. TOF-2d proves
with a test that the tenant is still bound at D1.

**The sources.** One closed list: `chat_tool`, `mcp_call`, `door_5xx`,
`issue_report` and `sandbox`. The last two come from reports (§8, §14).

**The surfaces.** One closed list: `chat`, `whatsapp`, `projects_assistant`,
`automation`, `mcp_edge`, `studio`, `app`, `other`.
- In a chat run, the run context names the channel. It binds no channel key
  today, so TOF-2c adds one at `executor.py` lines 6200 and 7542.
- At the gateway, the `X-Actor-Via` prefix maps: `chat:` to `chat`, `mcp:` to
  `mcp_edge`, `workflow:` and `system:` to `automation`. Any other value gives
  `app`. A forged header mislabels a surface only. It carries no text across.

### 4.3 The MCP map

C5, C6 and C7 test each row in order against every exception of the chain
that they caught. The chain holds the causes, the contexts and the groups, as
the walk of `mcp_tools._reason` does. The first row that matches wins. The last two rows
test the deepest exception that is an `Exception`. `asyncio.timeout` raises
`TimeoutError` from a `CancelledError`, so a timeout is never the deepest
exception. The map reads classes, numbers and frames only.

| Exception in the chain | Code | Owned by us |
|---|---|---|
| `TimeoutError`, or an `McpError` whose `.error.code` is 408 (`httpx.codes.REQUEST_TIMEOUT`, `mcp/shared/session.py` line 296) | `mcp_timeout` | No |
| `outbound_guard.AnswerTooLarge` or `_maf_mcp.McpListTooLong` (line 386). `AnswerTooLarge` comes before the next row, because it is a subclass of `OutboundRefused` (`outbound_guard.py` line 354) | `mcp_too_large` | No |
| `outbound_guard.OutboundRefused` | `mcp_refused` | No |
| `OSError`, `httpx.TransportError`, `httpx.HTTPStatusError`, `anyio.ClosedResourceError`, `anyio.BrokenResourceError`, or an `McpError` whose code is `CONNECTION_CLOSED` (-32000) | `mcp_unreachable` | No |
| `ToolExecutionException` with no cause (the server's `isError`), an `McpError` with any other code, or a `ListFailed(REASON_TOOL_ERROR)` of `shape_result` | `mcp_tool_error` | No |
| The deepest `Exception`: the last frame of its traceback is in one of the four trees of §3.2 rule 4, or its class has one of our roots of §3.2 rule 3 | `mcp_exception`, with the type and the site kept. The type and the site come from §3.2 rules 3 and 4 | Yes |
| The deepest `Exception`, of any other class | `mcp_unreachable` | No |

So a `KeyError` that our `_maf_mcp.py` or `mcp_tools.py` raises is
`mcp_exception`, not an outage. A server that lists too many tools is not
ours to fix, so `McpListTooLong` gives `mcp_too_large`, even with our root.

**An edge that we accept.** A fault of ours inside a handler for an outside
error has that error in its context. So it sorts as the row of that error.
The edge is wider than one row. Inside a handler of a library error that no
row names, our fault sorts as `mcp_unreachable` by the last row. The outage
rows need the context walk for the errors that the SDK wraps, so this spec
keeps the walk and accepts the edge.

So a customer server that is down gives codes that we do not own. It makes no
task at once (§7.2). Only a fault in our own client makes a task at once.

## 5. The store

Two tenant tables carry `organization_id` under FORCE RLS (R5 a). Three
platform tables carry no organization id. One migration of TOF-2b adds all
five. It takes the next free number at build time (R1), and
`bash scripts/premerge.sh` re-checks it before the merge. It only expands
(R6).

### 5.1 Tenant tables

**`failure_events`.** One row for each recorded failure, up to the sample cap
of §6.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key, `gen_random_uuid()` |
| `organization_id` | `uuid` | Not null. FK to `organization`, `ON DELETE CASCADE`. RLS |
| `occurred_at` | `timestamptz` | Not null, default `now()` |
| `fingerprint` | `text` | Not null. `^[0-9a-f]{16}$` |
| `source`, `surface`, `error_code` | `text` | Not null. Each a CHECK on its closed list |
| `tool`, `exc_type`, `code_site` | `text` | Not null. The platform fields of §3 |
| `tool_name` | `text` | Null. The real name when `tool` is a placeholder: the MCP server and tool, the skill, or the model's name. Tenant text. 200 characters at most |
| `member_email`, `run_id`, `session_id`, `request_id` | `text` | Null. Pointers into `agent_run`, `chat_message` and the request log |
| `model`, `release` | `text` | Null |
| `report_id` | `uuid` | Null. TOF-2g sets it when a member sends a report |

No column holds an argument, a result or an exception message. Indexes:
`idx_failure_events_org_time (organization_id, occurred_at)`,
`idx_failure_events_org_fp (organization_id, fingerprint, occurred_at)`,
`idx_failure_events_org_member (organization_id, member_email, occurred_at)`.

**`failure_org_days`.** One row for each tenant, fingerprint and day. The
platform counts tenants from it, with no tenant id on a platform row.
Columns: `organization_id uuid` (not null, FK, cascade, RLS), `fingerprint
text` and `day date` (UTC). Primary key `(organization_id, fingerprint, day)`.

### 5.2 Platform tables

The three tables go in `gen_tenant_migration.EXEMPT`, each with its own
reason:
- `failure_fingerprints` and `failure_daily`: "counts of platform code faults,
  no organization id, no tenant text".
- `failure_config`: "one row that names the internal org, which runs the
  failure jobs. It holds no customer data".

The migration revokes every table privilege from `PUBLIC`, `anon`,
`authenticated`, `service_role` and `acb_app`, as migration 276 does. Only the
functions of §5.3 reach them.

**`failure_fingerprints`.** One row for each fingerprint.

| Column | Type | Notes |
|---|---|---|
| `fingerprint` | `text` | Primary key. `^[0-9a-f]{16}$` |
| `fp_version` | `text` | Not null, default `'v1'` |
| `tool`, `error_code`, `exc_type`, `code_site` | `text` | Not null. Each a CHECK on the grammar of §3.2 |
| `sources`, `surfaces` | `text[]` | Not null, default `'{}'`. CHECK: each element is on its closed list |
| `first_seen_at`, `last_seen_at` | `timestamptz` | Not null. Cut to the hour |
| `first_release`, `last_release` | `text` | Null. `^[0-9a-f]{7,40}$` |
| `total` | `bigint` | Not null, default 0 |
| `state` | `text` | Not null, default `'new'`. `new`, `open`, `fix_merged`, `fix_live`, `closed` or `muted` |
| `task_id` | `uuid` | Null. A task of the internal org. No FK, because `pm_tasks` is a tenant table |
| `fix_merged_at`, `fix_live_at`, `closed_at` | `timestamptz` | Null |
| `fix_release` | `text` | Null |
| `reopened` | `int` | Not null, default 0 |
| `shown_total` | `bigint` | Not null, default 0. The total that the task shows now |

Index `idx_failure_fingerprints_state (state, last_seen_at)`.

**`failure_daily`.** Primary key `(fingerprint, day)`. Columns `occurrences
bigint`, `sources text[]`, `surfaces text[]` and `releases text[]` (each
release seen that day), with the CHECKs above. Index `idx_failure_daily_day
(day)`.

**`failure_config`.** One row: `internal_org uuid`. Only TOF-2f writes it,
with an owner-gated SQL on production. It names the org that may run the job
functions.

### 5.3 The functions

Each function follows the rules of migration 222. It is `SECURITY DEFINER`
with `SET search_path = pg_catalog, pg_temp`. It names each table with
`public.`. EXECUTE goes to `acb_app` only, and the file revokes it from
`PUBLIC` and the three Supabase roles.

1. **`failure_tally`** takes the fingerprint and its four fields. It also
   takes the source, the surface, the release and a count `p_n`.
   - It reads the tenant from `current_setting('app.tenant_id', true)`. No
     argument names a tenant (R5 e). With no tenant, it raises.
   - It computes the hash of §3.1 from the four fields again. A mismatch
     raises, so no caller can attach a made-up fingerprint.
   - `p_n` is 1 to 1000. The table CHECKs refuse a field off its grammar.
   - It upserts `failure_fingerprints` and `failure_daily`. It inserts the
     `failure_org_days` row of the bound tenant `ON CONFLICT DO NOTHING`.
   - It returns `void`.
2. **`failure_org_band(p_fp)`** returns `text`. It counts the distinct
   tenants of `failure_org_days` in a fixed window of 30 days. It returns the
   count only when the count is 3 or more. It returns `'<3'` for 1 or 2, and
   `'0'` for none. It returns NULL when its owner cannot see every row, as 222
   does. The window has no parameter, so no difference of two windows can show
   a count under 3.
3. **`failure_due(p_limit)`** returns platform rows only, for the sync job.
   Each row comes with its counts of 1, 7 and 30 days, and its releases.
4. **`failure_set`** writes the state columns of one fingerprint. They are
   the state, the task, the fix times, the fix release and the shown total.
   It never writes a field of §3.
5. **`failure_prune_daily(p_before)`** deletes old rows of `failure_daily`.

**Who may call the job functions.** Functions 2 to 5 raise unless
`app.tenant_id` equals `failure_config.internal_org`. They also raise when
`failure_config` has no row. So a session of any other tenant gets a refusal
from the database, not only from fence P6.
`failure_tally` is open to each tenant, for its own counts.

### 5.4 Retention

| Table | Kept | How |
|---|---|---|
| `failure_events` | 30 days | The prune hook of TOF-2e (§7.1) runs in the session of each org, as the app role, and deletes its old rows |
| `failure_org_days` | 90 days | The same prune |
| `failure_daily` | 400 days | The sync job calls `failure_prune_daily` |
| `failure_fingerprints` | While its task exists | It holds no tenant data |

The prune runs under `TOOL_FAILURE_TASKS`. So TOF-2f flips that flag
together with `TOOL_FAILURE_LOG_ORGS`. Otherwise event rows stay with no
prune.

No DEFINER function deletes a tenant row. An org purge cascades the tenant
rows. The platform rows hold no org id, so the purge has nothing to remove
there.

## 6. The write seam

**One function:** `record_tool_failure(...)` in
`packages/acb_common/acb_common/failure_log.py` (new, TOF-2b). Every capture
site of §4 calls it, and nothing else writes a `failure_*` table. The name
differs from `unattended.record_failure`, which stays the job ledger.

1. The caller passes the exception (or none), the source, the code, the tool
   name and the surface. It passes no tenant, and no set of built-in names.
   `record_tool_failure` takes the set that `set_builtin_names()` stored
   (§3.2 rule 1). With no set, a chat name gives `other`.
2. `record_tool_failure` reads the tenant with `current_tenant()`. With no
   tenant, or with the org off `TOOL_FAILURE_LOG_ORGS`, it returns at once.
3. It computes the platform fields at once, inside the `except` block, so
   the traceback is still there. It keeps no reference to the exception.
4. It puts one small record on a bounded queue of 500 in the process. A full
   queue drops the record, and logs `failure_log.dropped` with a count.
5. One drain task for each process takes the queue. It joins equal records
   within 2 seconds into one call with `p_n`.
6. For each tenant, the drain opens `tenant_session(org)`. It inserts the
   event rows and calls `failure_tally` in one transaction, with a timeout of
   2 seconds.
7. **The sample cap.** An org writes at most 20 event rows for each
   fingerprint and hour. A tenant Redis counter through `tenant_redis` holds
   the window (R5 c). The count goes on past the cap. If Redis fails, the
   drain writes the row.

**It never fails a tool call.** `record_tool_failure` never raises and never
waits on the database. A failure of the drain logs
`failure_log.write_failed` with the class only. The counts are a floor, not
an exact number, because a crash loses the queue.

**The release.** The gateway sets it once at start from
`build_info.build_sha()`. A process with no release writes NULL.

## 7. The task seam

### 7.1 The project and the writer

- **The project.** "Tool failures", in the internal org, under Metorite >
  Platform & CI/CD > Observability & Alerts, beside the IR epic (task 475).
  If that project cannot hold a child project, make it beside Observability
  & Alerts. TOF-2b makes it after the owner activates this spec, and not
  before.
- **Who sees it.** TOF-2b makes the project private. TOF-2f adds the grant to
  one staff group, which the owner names (TOF-Q3). No other member sees it
  (D12).
- **Its statuses.** TOF-2b makes the project with four statuses: Open
  (`todo`), Review (`in_progress`), Done (`done`) and Cancelled
  (`cancelled`). No status category names review (`core.py` line 339). So the
  job finds each lane by its name in this project. If a person renames a
  lane, the job finds no lane of that name. Then it moves no task, and it logs
  `failure_sync.lane_missing` with the name, until the name is back.
- **The writer.** `failure.sync` is a seed hook of the job queue
  (`docs/job_queue.md` §5c), every 15 minutes. It returns for each org except
  `TOOL_FAILURE_ORG_ID`. It starts no lifespan loop. If `JOB_QUEUE` is off on
  production, TOF-2f asks the owner to flip it. The dedupe key of the bucket
  gives one live job.
- **Its registration.** TOF-2e registers `failure.sync` with
  `mode="detached"`, `idempotent=True`, `effect="write"`, and a policy that
  names D225 and the internal org only. See `gateway/jobs/worker.py`
  `register` and the fence `test_job_effects_fence.py`. It imports the module in
  `gateway/main.py` beside the other seed consumers. It adds a consumer line
  to `docs/job_queue.md` §5c.
- **Why a second try is safe.** For each fingerprint, the job makes or moves
  the task and calls `failure_set` in one transaction of
  `tenant_session(TOOL_FAILURE_ORG_ID)`. So a second try reads the task id
  and makes no second task. This is the reason that `register` asks for
  `idempotent=True`.
- **The prune.** The tenant prune of §5.4 is a `worker.prune_hooks` hook
  (`docs/job_queue.md` §5b). The daily `jobs.prune` runs it in the session of
  each org. The hook runs its deletes in a savepoint
  (`db.begin_nested()`), and it logs and swallows a failure. A hook that
  raises would fail the whole prune of that org. It would also roll back the
  delete of the `jobs` rows (`worker.py` lines 415 to 418).
- **The principal.** The job binds the internal org with
  `tenant_session(TOOL_FAILURE_ORG_ID)`. It makes a task through
  `check_task_values` and `create_task_in`, as the member
  `TOOL_FAILURE_RUN_AS` (TOF-Q3), with `source = 'automation'`. It moves a
  status through `apply_status_transition` with
  `created_by = "system:tool-failures"` and `automation = True`, as GH-6 does.
- **What it reads.** Platform rows from `failure_due`, and the band from
  `failure_org_band`. In the internal org, it also reads its own tasks, their
  `pull_request` references and their status changes. It reads no tenant row
  of another org.

### 7.2 When a fingerprint gets a task

| Code | The job makes a task when |
|---|---|
| Owned by us (§3.2 rule 2) | It first sees the fingerprint |
| Not owned by us | The count reaches 20 in 24 hours on 2 days in a row, or the band reaches 3 organizations |

The limits are settings (§11). A fingerprint under the limit keeps its counts
and gets no task. A NULL band counts as "cannot tell": the band rule does not
apply, and the task shows "unknown". `auth_401` and `unknown_tool` carry no
type and no site, so expect one task for each tool.

### 7.3 The text of the task

Every value comes from a platform row. Each value is in a code span. §10.1
lists each field that the task may show.

- **Title:** `Tool failure · <tool> · <error_code> · <fingerprint>`.
- **Description**, in five short blocks:
  1. What failed: the tool, the code, the exception type and the code site.
  2. Where: the sources. The surfaces show only when the band is 3 or more.
  3. Counts: the total, the last 24 hours, 7 days and 30 days, and the
     organizations in 30 days as a band.
  4. Seen: the first and the last date, never an hour, and the first and the
     last release.
  5. The rule: "Fix the cause at the code site. Move this task to Review when
     the pull request opens. Do not move it to Done. The job closes it 7 days
     after the fix is live, with no repeat."
- **Comments** only at events: a new release saw it, a state change, a
  reopen, a close, and a report that a member sent (§8).

The job rewrites the counts block at most once an hour. The text holds no
free text from a tenant, so it has no place for an injected instruction.

### 7.4 The states

| From | Event | To | What the job does |
|---|---|---|---|
| `new` | The rule of §7.2 holds | `open` | Makes the task |
| `open` | The task has a `pull_request` reference with `merged_at` | `fix_merged` | Writes `fix_merged_at` |
| `fix_merged` | The running build holds the fix | `fix_live` | Writes `fix_live_at` and `fix_release`. Comments the close date |
| `fix_live` | A count of a release that holds the fix, in an hour after the hour of `fix_live_at` | `open` | Moves the task to the Open lane. Comments "Repeat after the fix". Adds 1 to `reopened` |
| `fix_live` | 7 days pass with no such repeat | `closed` | Moves the task to the Done lane. Comments the release |
| `closed` | Any new count of a release that holds the fix | `open` | Reopens the same task, as above |
| `open`, `fix_merged` or `fix_live` | A person or an agent moves the task to the Done lane | the same | Moves it back to the Review lane, each time. Comments "The job closes this task after the watch" |
| any | A person moves the task to the Cancelled lane, archives it or deletes it | `muted` | Stops all writes to the task. The counts go on |
| `muted` | A person moves the task back to the Open lane | `open` | Writes again |

**"Holds the fix".** A release holds the fix when its commit time is at or
after `fix_merged_at` minus 10 seconds. `main` takes squash merges in order.
On PRs 2426, 2438 and 2447, `merged_at` came 0 to 1 second after the
committer time.
- The new helper `build_info.build_committed_at(sha)` runs
  `git show -s --format=%ct <sha>` once for each SHA, with a timeout of 5
  seconds. It caches the answer.
- Only the sync job calls it, never a request handler. So the rule of
  `build_info.py` against a subprocess in a request still holds.
- The running build is the SHA that `build_sha()` cached at start, not the
  `HEAD` of today, because `HEAD` moves before the restart.
- The reopen rule reads `last_seen_at` and `last_release` of the platform
  row, and `failure_daily.releases` of each day since `fix_live_at`.
- A count from an old process on an old release never reopens a task. A NULL
  release never holds the fix.
- With no answer from `git`, the state does not move.

**One fingerprint, one task, always.** The job never makes a second task for a
fingerprint.

### 7.5 One closer

GH-6 (`github_merge._rule_for_task`) moves an agent-worked task to done at the
merge. That would close a failure task 7 days early. TOF-2e adds one step to
that rule: skip a task of the project `TOOL_FAILURE_PROJECT_ID`. The sync job
is then the one closer of the project.

TOF-2e also adds a pointer line in `github_integration.md` §12.3, a case in
`test_github_merge_r8.py`, and one line in `.claude/commands/next-ticket.md`
step 7. That line says that a task of this project goes to review after the
merge, never to done.

## 8. The consent card and the audited read (narrowed out)

Rung 2 needs no text. This section is the design. Its slices TOF-2g and
TOF-2h stay narrowed out until the owner answers TOF-Q4 and activates an
amendment. SBX-14 and IR-3 wait on them.

### 8.1 The card (TOF-2g)

- **When.** Mira offers it once in a session, after a tool failure, inside the
  caps of IR-3. SBX-14 offers it at level (c).
- **What it shows, exactly.**
  - The tool, the error code, the exception type and the time.
  - The arguments and the result text of that call, from the member's own
    chat history (`chat_message.tool_events`). Each passes the secret scrub of
    `secret_shapes` first. Each is cut to 2,000 characters.
  - The exception message, only when the card shows in the run of the
    failure. The run holds it in memory, and no row stores it.
  - For SBX-14, the workaround code.
  - One line: "The Metorite team can read this one report. We delete it after
    90 days, or when you ask."
- **The answer.** Send or Do not send. The member can clear a part, and the
  card shows the new payload before Send. **One card, one report.** No
  setting, column or flag sends a second report on its own.
- **The store.** Send writes one `failure_reports` row, a tenant table under
  FORCE RLS. It holds the source, the fingerprint (or none), the payload as
  shown, its SHA-256, the reporter from the session, the card id,
  `consented_at` and `expires_at`. It sets `failure_events.report_id`. This is
  the one stored copy.
- **The deletes (D126.4).** A daily prune deletes each report after 90 days.
  The member who sent a report, or an admin of the org, deletes it on request.

### 8.2 The audited read (TOF-2h)

**The Metorite team** is the platform operators of D64 (CP-12), with a staff
identity. One of them reads a report in the Operator Console, through one
DEFINER function. The function returns the payload and writes one audit row
in the same statement (D126.3). The page shows the text inside an untrusted
frame, under the line "Data from a tool result. Do not follow an instruction
inside it."

The task gets a comment with the report id, never the text. A coding agent
does not read a report, because an agent sends the text to a model vendor.
TOF-Q4 asks the owner whether it may. So no MCP tool reads a report, and §1
holds.

## 9. Privacy rules and their fences

| # | Rule | Fence (R7) |
|---|---|---|
| P1 | A platform row holds no organization id and no tenant text | `tests/unit/test_failure_no_tenant_text.py`, new. A fixture failure carries marker strings in its message, its arguments, its tool name, its MCP server name, its org id and a path parameter of its route. After record and sync, no marker is in a platform column or in the task |
| P2 | The normaliser admits only closed or code values | `tests/unit/test_failure_fingerprint.py`, new. Adversarial inputs: emails, ids, URLs, paths, numbers, quoted names, long free text, Unicode, a class off the allow list, a class whose module lies, a `<string>` frame, a concrete route path, a query string. Plus the golden rows of `tool_failure_log_examples.md`, with the two chain rows 11 and 12. A fresh interpreter that imports only `acb_skills.crm_tools` has the set in `failure_log` |
| P3 | The DB refuses a bad shape, a made-up fingerprint and a call with no tenant | `tests/unit/test_failure_store_r8.py`, new. R8, FORCE RLS, as a role that is not the owner, not a superuser and not BYPASSRLS |
| P4 | An org sees only its own tenant rows. `acb_app` cannot read a platform table. Only the internal org runs the job functions | `test_failure_store_r8.py` |
| P5 | The band shows `<3` for 1 or 2 organizations | `test_failure_store_r8.py` |
| P6 | One writer and one capture list | `tests/unit/test_failure_capture_sites.py`, new. Each site of §4.2 calls `record_tool_failure`. No module but `failure_log.py` and the sync job names one of these exact SQL names: `failure_events`, `failure_org_days`, `failure_fingerprints`, `failure_daily`, `failure_config`, `failure_reports`, `failure_tally`, `failure_org_band`, `failure_due`, `failure_set`, `failure_prune_daily`. The fence matches whole words, so `failure_text` and `record_failure` do not match. The fence reads `apps/` and `packages/`. `scripts/gen_tenant_migration.py`, `infra/postgres/` and `tests/` are out of its scope. `failure_tally` is a new name, because `push/store.py` already uses the column `failure_count` |
| P7 | The logger never fails a tool call, and it holds its bounds | `tests/unit/test_failure_log_queue.py`, new. With the store down, each tool answers as before. A full queue drops and counts. Equal records join into one `p_n`. The sample cap stops event rows at 20 and keeps the count |
| P8 | The coverage fence holds the tenant tables, and EXEMPT holds the platform tables with a reason | `tests/unit/test_tenant_coverage.py` |
| P9 | A row older than its retention is gone after the prune, in each org, as the app role | `test_failure_store_r8.py` |
| P10 | One consent, one report, and the stored payload equals the shown payload | `tests/unit/test_failure_report_consent.py`, new in TOF-2g |

Each new fence is red first (`engineering_practice.md` §4).

## 10. Privacy and threats

### 10.1 What crosses a tenant

This list is the whole of what leaves a tenant with no consent. D225 point 5
names "the counts, the tool name and the error class". This list adds code
facts and coarse times, so the owner confirms it in TOF-Q2 before TOF-2b.
The code site is an input of the v1 hash, so a later "no" means a v2
fingerprint and new work in TOF-2b to TOF-2e.

| Field | Platform row | Task text |
|---|---|---|
| The fingerprint, `tool`, `error_code`, `exc_type`, `code_site` | Yes | Yes |
| `sources` | Yes | Yes |
| `surfaces` | Yes | Only when the band is 3 or more |
| Counts by day, and the total | Yes | Yes |
| The band of organizations | Computed, never stored | Yes |
| First and last seen | The hour | The date only |
| Releases | Yes | Yes |
| Organization id, member, run, tool name, any text | Never | Never |

### 10.2 Threats

| # | Threat | Answer |
|---|---|---|
| T1 | A rare fingerprint names a tenant | No org id on a platform row. The band hides 1 and 2. The task shows dates, and shows surfaces only at a band of 3. That does not hide a tool or a route that one org alone uses, such as a Zoho mirror tool or a feature behind an `*_ORGS` flag that names one org. Then the tool and the day counts name that org by inference, and under a band of `<3` the day counts are its activity. On a siloed box, a release names its one tenant too. TOF-Q2 tells the owner this |
| T2 | Tenant text enters through the message | The message is not an input (§3.3). Fences P1 and P2 |
| T3 | Tenant text enters through a name or a path | Only a registry name and a declared route template pass. MCP, Studio and model names become placeholders |
| T4 | Tenant code names a class or a frame | The module allow list with the attribute walk, and frames in the four trees only |
| T5 | Error text injects an instruction into a coding agent | The task holds no tenant text. A report is read only by a person, in an untrusted frame (§8.2). The Metorite MCP returns comments as `untrusted_text` |
| T6 | A caller forges a tenant or a fingerprint | `failure_tally` reads `app.tenant_id` and computes the hash again |
| T7 | A failure loop floods the database | The queue bound, the join of equal records, and the sample cap. Fence P7 |
| T8 | A DEFINER function leaks across tenants | Each returns platform columns or a band. Only the internal org runs the job functions (§5.3) |
| T9 | A secret reaches a report | The `secret_shapes` scrub before the card shows, and the 90-day delete |
| T10 | A report goes out with no consent | `failure_reports` needs a card id and the reporter from the session. Fence P10 |

## 11. Flags and settings

Each flag is off by default. A new flag goes in at its sorted place in
`settings.py` and `.env.example`.

| Name | Kind | Default | What it does |
|---|---|---|---|
| `TOOL_FAILURE_LOG_ORGS` | `*_ORGS`, reloads (`docs/flag_reload.md`) | empty | The orgs whose failures the sites record. `*` is every org |
| `TOOL_FAILURE_TASKS` | bool, needs a restart | off | Runs `failure.sync` and the prune |
| `TOOL_FAILURE_REPORT_ORGS` | `*_ORGS` | empty | The consent card (TOF-2g) |
| `TOOL_FAILURE_ORG_ID`, `TOOL_FAILURE_PROJECT_ID`, `TOOL_FAILURE_RUN_AS` | settings | empty | The internal org, the project and the member of §7.1. With any one empty, the job does nothing |
| `TOOL_FAILURE_EXTERNAL_MIN`, `TOOL_FAILURE_CLOSE_DAYS` | settings | 20, 7 | The limit of §7.2 and the watch of §7.4 |

## 12. Owner gates

| Act | Gate |
|---|---|
| Activate this spec | The owner |
| Build every slice, dark, after the owner activates this spec. TOF-2b also waits for TOF-Q2 | AGENT-SAFE |
| Make the "Tool failures" project, after the owner activates this spec | AGENT-SAFE |
| Take the migration number at build time (R1) | AGENT-SAFE. The home box lanes book it with `~/lanes/reserve_migration.sh`, which is not in this repository |
| The production apply of the migration, at a release slot | OWNER-GATE (`deploy`). Confirm the backup first (R6) |
| Write the `failure_config` row on production | OWNER-GATE (a production one-off) |
| Set the three ids of §11 on the box | OWNER-GATE (`env-write`) |
| Flip `TOOL_FAILURE_LOG_ORGS`, `TOOL_FAILURE_TASKS` or `TOOL_FAILURE_REPORT_ORGS` | OWNER-GATE (`enforcement-flip`) |
| Add the org of a customer to a flag | OWNER-GATE until the owner answers TOF-Q2 |

A dated line in `.claude/OWNER_GRANTS.md` can open a gate (CLAUDE.md §3a).

## 13. The ladder and the slices

### 13.1 Rung 2 and its demo

**Demo task 3417.** A tool error on production becomes one task with counts.
A fix merges, and the next release shows the task as "fix live, closes on
<date>". The job closes the task 7 days later with no repeat (TOF-Q1).

### 13.2 The slices

The planner made each task in Backlog under epic 3393 (2026-11-16) and wrote its number here.

| Slice | Task | Rung | What | Done when | Fence (R7) | Tier · gate | Depends on |
|---|---|---|---|---|---|---|---|
| **TOF-2a** | 3416 | 2 | This spec, its INDEX row, the pointers in `agent_sandbox.md`, and the board rows of `work_plan.md` §2.1 | The lint passes. INDEX names the spec. An Opus audit gives GO | `test_index_completeness.py`, `test_doc_names_resolve.py`, ste-lint | HIGH, docs · AGENT-SAFE | none |
| **TOF-2b** | 3543 | 2 | The normaliser (§3), the migration of §5, `record_tool_failure` (§6), the EXEMPT entries, the generated tenant set, `builtin_tool_names()`, and the private project with its four statuses (§7.1). Files: `acb_common/failure_fingerprint.py`, `acb_common/failure_log.py`, `acb_skills/builtin_tools.py` (new), `acb_skills/__init__.py`, `infra/postgres/<next>_failure_store.sql`, `scripts/gen_tenant_migration.py`, `infra/postgres/generated/`, `settings.py`, `.env.example` | The 12 golden cases pass. A fixture call writes one event row and one count. A call with no tenant, a bad field or a made-up fingerprint gets a refusal. Another org gets a refusal from the job functions | P2, P3, P4, P5, P7, P8 | HIGH · AGENT-SAFE dark. The apply is OWNER-GATE | The owner activates this spec and answers TOF-Q2, because the code site is an input of the v1 hash |
| **TOF-2c** | 3544 | 2 | The chat sites C1 to C4, the C1 skip rule, the `channel` key, and the two status errors to convert (`skill_my_tasks._GatewayError`, and agent-crm `_MirrorBlocked`, a 409). Out of scope: `knowledge_tools.py`, whose tools are core tools. Files: `acb_skills/pack_tool_guard.py`, `acb_skills/unknown_tool.py`, `acb_common/tool_refusals.py`, `skill_my_tasks/core.py`, `apps/agents/agent-crm/agents.py`, `orchestrator/executor.py` | Each site writes one record for one failure. A refusal of §4.1 writes none. A gateway 5xx seen in the chat writes none. A 401 from an outside host writes none. A 401 from the gateway writes `auth_401`. A connect error to an outside host writes `outside_unreachable`. A pack tool that catches a gateway 500 and raises `KeyError` with no `from` writes none | `test_failure_capture_sites.py`, new. `test_every_pack_tool_answers_text.py`, `test_unknown_tool_answer.py`, `test_tool_argument_shapes.py` | STANDARD, Opus review: a shared seam · AGENT-SAFE dark | TOF-2b |
| **TOF-2d** | 3545 | 2 | The door sites D1 and D2, the route set of §3.2 rule 1, and the MCP sites C5 to C7 with the map of §4.3. Files: `gateway/main.py`, `orchestrator/_maf_mcp.py`, `gateway/connections/mcp_tools.py` | A route that raises writes one `unhandled_500` with the tenant of the request. A route that answers 500 writes one `route_500`. A `db_busy` 503 writes none. A concrete path writes `route:other`. An MCP timeout writes `mcp_timeout`, with the server in `tool_name` only. An `isError` answer writes `mcp_tool_error`, in the chat and in `shape_result`. A refused connect writes `mcp_unreachable`. A Studio live read that times out writes one `mcp_timeout` and no `route_upstream`. A `KeyError` raised in our MCP client code writes `mcp_exception` with its site. A server that lists too many tools writes `mcp_too_large` | `test_failure_capture_sites.py`, `test_db_busy.py`, `test_maf_mcp_confirm.py`, `test_studio_mcp_tools.py` | HIGH: the tenant seam · AGENT-SAFE dark | TOF-2b |
| **TOF-2e** | 3546 | 2 | `failure.sync` as a seed hook (§7.1), the task text, the states of §7.4, `build_committed_at`, the prune of §5.4 under `TOOL_FAILURE_TASKS`, the GH-6 skip and the `next-ticket.md` line. Files: `gateway/failures/sync.py` (new), `gateway/main.py`, `docs/job_queue.md` §5c, `gateway/build_info.py`, `routes/projects/github_merge.py`, `github_integration.md` §12.3, `.claude/commands/next-ticket.md` | On a real database: one task for each fingerprint. A repeat updates the counts. A merge, a live build and 7 clean days close it. A repeat of a release that holds the fix reopens it, and a repeat of an old release does not. A move to Done goes back to Review. A second try of the same job, after a try that failed, makes no second task. When the deletes of the TOF-2e prune hook fail, the `jobs` rows of that prune stay deleted. GH-6 skips the project. After the prune, in each org and as the app role, `failure_events` rows older than 30 days and `failure_org_days` rows older than 90 days are gone. `failure_daily` rows older than 400 days are gone | `tests/unit/test_tool_failure_tasks_r8.py`, new. P1, P9. `test_github_merge_r8.py` | HIGH: writes into the internal org · AGENT-SAFE dark | TOF-2b |
| **TOF-2f** | 3547 | 2 | Turn it on: the `failure_config` row, the ids, the grant of the TOF-Q3 group on the project, `JOB_QUEUE` if it is off, then `TOOL_FAILURE_LOG_ORGS` and `TOOL_FAILURE_TASKS` together, for the internal org first | One real failure on production makes one task. Evidence: the ledger line, the deployed SHA, the `failure_log` line and the task | none: an act | OWNER-GATE | TOF-2c, TOF-2d, TOF-2e, TOF-Q2, TOF-Q3 |
| **TOF-2g** | 3548 | Later | The consent card, `failure_reports` and its two deletes (§8.1) | Narrowed out, not dispatchable until an owner-activated amendment. It then names its files | P10 | HIGH | TOF-2b, TOF-Q4 |
| **TOF-2h** | 3549 | Later | The audited read (§8.2) | Narrowed out, as TOF-2g | `tests/unit/test_failure_report_read_r8.py`, new | HIGH | TOF-2g |
| **TOF-2i** | 3550 | Later | The Operator Console page of counts, with SBX-16 and IR-5 | Narrowed out, as TOF-2g. It needs a rung demo first | none yet | STANDARD | TOF-2e |

**The order.** TOF-2b first. Then TOF-2c, TOF-2d and TOF-2e can run in
parallel. TOF-2f and the demo come last. TOF-2d and CSY-2 both edit
`_McpPermissionGate.process`, so the second to start takes the first as its
base. TOF-2d and TOF-2e both edit `gateway/main.py`, with the same rule.

A test file marked "new" does not exist yet. The slice that names it writes
it, red first.

## 14. Coordination

### 14.1 Issue reports (IR-0, task 555, and IR-4)

- **One store.** An issue report that a member sends is a `failure_reports`
  row with `source = 'issue_report'`. IR-0 adds its kinds and its fields
  (screenshot, route, chat bundle) as nullable columns of that table (R6).
  IR-0 adds no second report store.
- **Who makes the table.** TOF-2g owns the `CREATE` of `failure_reports`.
  IR's store slice adds its columns after TOF-2g lands. If IR needs the
  table first, IR's store slice needs its own spec, and the planner moves
  TOF-2g ahead.
- **One fingerprint.** IR-4 calls `failure_fingerprint` for a report that
  holds an error. A failed API call joins the door fingerprint through
  `failure_events.request_id`. IR-0 maps a browser error to a `tool` in the
  grammar of §3.2, from a closed set such as the route templates.
- **One task.** A report with a fingerprint becomes one comment on the task of
  that fingerprint, never a new task. A report with no error (a bad answer,
  an idea) follows the task rule of IR-4.
- **One card.** IR-3 extends the card of §8.1 with the chat bundle, and adds
  no second card.
- **No wait on IR-0.** The epic text says that IR-0 waits on O16. O16 of epic
  475 asked whether a report may carry a chat, and D126 answered it on
  2026-10-27. The O16 on `main` today is a question of AI Studio. So this spec
  waits on neither, and the IR-0 task text is stale.

### 14.2 The rest

- **The job incident ledger (AUTO-2).** `unattended.record_failure` keeps one
  open incident for each job and error class in `unattended_incidents`. It
  stays a separate ledger now, for three reasons:
  - Its unit is a job run with a slot and a state, not a tool call.
  - It is dark behind `UNATTENDED_LEDGER`, and it has its own owner spec.
  - A join now would couple two dark features.

  The seam for later is one call: `unattended.record_failure` also calls
  `record_tool_failure` with a new source `job`, and the job kind as the
  `tool`. `check_job` already holds that name to a code grammar.
- **SBX-14 (task 3429).** A level (c) report is a `failure_reports` row
  through TOF-2g, with `source = 'sandbox'`. The workaround retires when its
  fingerprint reaches `fix_live`.
- **LOOP-16 (task 3456).** The learner reads the member's own
  `failure_events` rows as counts by `error_code` and `tool`, and no
  `tool_name`.
- **CSY-2 (task 3401).** The `mcp.call` line and the C5 capture sit in one
  method. The `mcp_*` codes keep the names of the `REASON_*` constants, so
  the two agree.
- **HRD-1 and HRD-3.** The capture adds no text. TOF-2c changes one word in
  the answers of two transports: the class name in "The tool failed
  (<kind>)". The HRD-3 shape and its fences stay. `chat_failure_modes.md`
  owns that text, so TOF-2c names the change in its pull request.
- **D205.** The block table of `building_blocks.md` §2 has no failure block.
  D225 is the owner's answer that a store must exist. A row in that table
  changes an owner-approved contract, so the planner adds it with the owner's
  OK. No TOF slice edits `building_blocks.md`. Everything else extends a seam
  that exists: the tenant session, the task creator, the job queue, the Redis
  wrapper and the secret shapes.

## 15. Questions for the owner

| # | Question | Recommendation | Why |
|---|---|---|---|
| **TOF-Q1** | The demo waits 7 days for the close. May demo 3417 show the "fix live" state, and the close a week later? | Yes. Keep 7 days on production | A shorter watch would close a task while a weekly job can still fail |
| **TOF-Q2** | May the fields of §10.1 leave each customer org with no consent, from day one? D225 names "counts, the tool name and the error class". §10.1 adds the code site, the sources, the surfaces at a band of 3, dates, hours on the platform row, and releases. A tool or a route that one org alone uses names that org by inference, with its day counts (T1). Does the privacy page need a line? | Yes to the list. Add one line through the WS-33 flow, which the owner approves | Each added field is a fact of our code or a coarse time. None is tenant text. A coding agent needs the code site to fix the cause |
| **TOF-Q3** | Which member does the sync job act as, and which staff group sees the project? | A new member `tool-failures@hathilabs.com`, which the owner adds, and one staff group that the owner names | D140.2 needs a named member. A member write is the owner's act |
| **TOF-Q4** | May a coding agent of a staff member read a consented report? | No, for now. A person of the Metorite team reads it in the Operator Console | D126.3 lets Metorite staff read a report. An agent sends the text to a model vendor, which the member did not consent to |

## 16. Verification commands

```bash
# TOF-2a, the docs
node .claude/hooks/ste-lint.mjs project-docs/specs/tool_failure_log.md
node .claude/hooks/ste-lint.mjs project-docs/specs/tool_failure_log_examples.md
timeout 300 uv run pytest -p no:cacheprovider --timeout-method=thread \
  tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q

# TOF-2b to TOF-2e, hermetic
timeout 600 uv run pytest -p no:cacheprovider --timeout-method=thread \
  tests/unit/test_failure_fingerprint.py tests/unit/test_failure_capture_sites.py \
  tests/unit/test_failure_log_queue.py tests/unit/test_every_pack_tool_answers_text.py \
  tests/unit/test_unknown_tool_answer.py tests/unit/test_maf_mcp_confirm.py \
  tests/unit/test_studio_mcp_tools.py tests/unit/test_db_busy.py -q

# The R8 suites need a database. A skip is not a pass
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest -p no:cacheprovider --timeout-method=thread \
  tests/unit/test_failure_store_r8.py tests/unit/test_tool_failure_tasks_r8.py \
  tests/unit/test_failure_no_tenant_text.py tests/unit/test_github_merge_r8.py \
  tests/unit/test_tenant_coverage.py -q
```
