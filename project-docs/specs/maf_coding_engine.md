# MAF coding engine and sandbox terminal

<!-- ste-tier: strict -->

**Status: ACTIVE. Spec only, nothing built.** Owner decision, 2026-10-03.
Board row **WS-43**. This spec records **D82** and **D83**.

Verified against code on 2026-10-03 at `main` `f0264ce8`.

## 0. One paragraph

`code_task` stops using the GitHub Copilot SDK. A MAF harness session takes
its place. The session runs in the gateway process, and it holds the model
loop and the model key. Its shell commands run in a Docker container. Each
organization, agent and chat thread gets its own container.

The container has no network, and it sees only the tenant working dir at
`/workspace`. An agent gets network access only when a person approves a
request in the chat. One module, the sandbox broker, owns the Docker socket
and every container. The app-builder agent moves to the same engine.
Everything ships dark, behind a flag. The Copilot path stays until an eval
through the Router shows parity.

## 1. The owner decision

On 2026-10-03, in chat, the owner approved this recommendation:

> "build the MAF coding agent with a container per org, no network by
> default, network opened only on approval"

That approval is the source of D82 and D83. `work_plan.md` §3 holds the
decision records. This spec holds the design.

## 2. D82 — `code_task`'s engine moves from the Copilot SDK to MAF

**What changes.** `agent_architecture.md` §11 made MAF the one runtime and
kept the Copilot SDK as the engine behind `code_task`. D82 replaces that
engine. `code_task` becomes a bounded MAF harness session
(`create_harness_agent`). The session has the file tools of MAF, a skills
provider, and a `run_command` tool that runs in the sandbox.

**What stays.** §11's decision stands: one runtime, MAF, and coding is a tool
and not a runtime. `code_task` keeps its name, its signature and its contract
(`agent-data/SCRIPTS.md` first, edit in place, report at the end). §11 keeps
its text, and a dated amendment box links it to this spec.

**How it ships.** Behind `MAF_CODING_SCOPE`, default empty. The Copilot path
stays the default until WS-43h shows parity through the Router.

## 3. D83 — T2 is un-parked for the sandbox terminal only

**What changes.** D10.1 parked the T2 container tier, and D16 made it a
precondition of the pooled cutover. D83 un-parks T2 for one thing: the
container that runs the shell commands of the coding engine. That container is
WS-43's sandbox terminal, as §7 describes it.

**What stays parked.** Everything else that D16 parks:

- P5-c of `permissions_sandbox_b6.md`, which lifts a whole agent run into a
  container, with a tool-proxy RPC, a per-agent image and a warm pool.
- P5-d, the default-deny tightening that waits on P5-c.
- P5-b.3, the scoped gateway key.
- T2 as a precondition of the §5.1 pooled cutover (MT-0c-2).

An agent asked to build any item in that list still refuses it by name.

**Why the un-park is narrow.** The agent loop, the platform tools and every
key stay in the gateway process. Only the text of a command crosses into the
container. So the container needs no network and holds no secret. P5-c is a
different and larger thing.

## 4. What the code does today

Measured on 2026-10-03 at `f0264ce8`.

### 4.1 `code_task`

- `packages/acb_skills/acb_skills/code_tools.py:340` defines `code_task`.
  It calls `run_copilot_code_session`, then sweeps changed files into the blob
  store, then commits repo changes for approval.
- `apps/services/orchestrator/orchestrator/code_session.py:71` builds a fresh
  `MetoriteCopilotAgent` on every call. Each call pays `client.start` and
  `stop` (§5.3).
- `code_task` is in `_CORE_STANDARD_TOOL_NAMES`
  (`orchestrator/_tool_injection.py:55`) and in the core skill family, which
  no toggle removes (`acb_skills/skill_families.py`). So every agent with
  injected platform tools holds it, and D82 changes the engine for all of
  them at once.
- `manifest.py:70` puts `code_task` in `SHELL_TOOLS`, so an agent that holds
  it derives tier T2.

### 4.2 The Copilot sandbox

`orchestrator/copilot_sandbox.py:123` starts the `copilot` CLI in a
container. It ships OFF behind `copilot_sandbox_scope`
(`acb_common/settings.py:515`, default `""`). Read against the code:

- The image `Dockerfile.copilot-sandbox` has no `USER` line, so the CLI runs
  as root.
- The `docker run` at `copilot_sandbox.py:153-171` passes no `--network`, so
  the container is on the default bridge with full egress.
- It passes no `--read-only`.
- The host sends the gateway LLM key (`llm_api_key`) to the CLI in the
  `create_session` payload (`code_session.py:97-105`).
- The CLI runs the agent's shell commands inside that same container.

So code that the model writes runs as root, beside a process that holds a
key, on a network. The container cannot have `--network none`, because the
CLI must reach the model through the gateway.

**MAF changes that.** The loop and the key stay outside the container. The
container gets commands, not a model client, so it can have no network.

### 4.3 app-builder

- `apps/agents/agent-app-builder/agents.py:47` returns a `GitHubCopilotAgent`,
  although `config.json` declares `"runtime": "maf"`.
- It uses the Copilot CLI's own shell and file tools, in the session
  workspace that `executor._session_workspace_override` (`executor.py:1165`)
  resolves under the Custom Apps root.
- `executor._maybe_sandbox_session_workspace` (`executor.py:1492`) puts its CLI
  in a sticky container per thread when `app_builder` is in the scope.
- Its T2 build runs `node build_t2.mjs`, and it reads a vendor cache from
  `t2_vendor_dir()`. So its sandbox needs Node.

### 4.4 MAF on main

- `uv.lock` holds `agent-framework-core` 1.19.0.
- `agent_framework._harness` has `create_harness_agent`, with the parameters
  `file_access_store`, `skills_provider`, `todo_provider`, `disable_todo` and
  `shell_executor`.
- `FileAccessProvider` gives eight tools: `file_access_read`,
  `file_access_read_lines`, `file_access_write`, `file_access_replace`,
  `file_access_replace_lines`, `file_access_ls`, `file_access_grep` and
  `file_access_delete`.
- `FileSystemAgentFileStore` resolves each path under one root and refuses a
  symlink.
- The MAF docstring marks file access and the shell tooling as experimental.
  `LocalShellTool` and `DockerShellTool` live in the pre-release package
  `agent-framework-tools`. The harness wires them only for a client that
  supports a shell.

### 4.5 The gateway

The gateway serves `/v1/chat/completions` and `/v1/embeddings`
(`gateway/main.py:552`, `:1669`). It serves no Responses API. So the harness
cannot use a hosted shell tool, and WS-43 builds its own `run_command`
function tool. Every MAF agent reaches the model through
`OpenAIChatCompletionClient` with `acb_llm.attribution.attributed_openai`,
which stamps the member, app and run on each request.

### 4.6 The tenant working dir

`projects_ai_chat.md` §21.15 and §21.16 built the seam that WS-43 mounts:

- A shared agent's run works in `state/<agent>/<slug of o:<org>>/`.
  `agent_paths.tenant_instance` (`agent_paths.py:172`) makes the key, and
  `agent_paths.state_root` (`:140`) is the root.
- `executor._resolve_run_workspace` (`executor.py:1322`) gives the dir and the
  blob-store key. A shared run with no tenant raises `RunWorkspaceRefused`.
- The tenant comes from the run binding (`_current_run_org()`), never from
  the request body.
- `acb_skills.write_artifact.artifact_context()` reads the run's own context.
  It holds `workspace_root`, `instance` and `session_id`, and it is empty
  when no run is bound.

## 5. Evidence — the spike of 2026-10-03

A local spike tested the design. It is not in the repo. Its files are in the
worktree `.claude/worktrees/agent-ab617bcee46c6f7db/spikes/maf_sandbox/` on
the owner's dev box. The numbers below come from `runs/*.summary.json`,
`bench_out.txt` and `bench_out2.txt` there.

### 5.1 What passed

The spike ran `create_harness_agent` with `FileAccessProvider`,
`SkillsProvider` and the todo provider, plus a Docker sandbox. It used no
Copilot code.

| Task | What it tests | Model | Result | Wall time |
|---|---|---|---|---|
| T1 | Write, run and fix a script | `gemini-flash-latest` | Pass. 2 failed runs, both fixed | 87.0 s |
| T2a | Create a skill | `gemini-flash-latest` | Pass | 39.5 s |
| T2b | Reuse the skill in a new session and a new container | `gemini-flash-latest` | Pass. 1 `load_skill`, 1 skill script run | 12.4 s |
| T3a | Install a package with no network | `gemini-flash-latest` | Failed safely. It said "did not work" and made no fake file | 48.8 s |
| T3b | Install through the approval-gated allowlist | `gemini-flash-latest` | Pass. 1 approval, grant in 1.27 s, pip through the proxy, PDF made | 71.6 s |
| T4 | Escape probes | `gemini-flash-latest` | Refused. Host path absent, `../../.env` absent, no DNS, key not in the transcript | 17.9 s |

### 5.2 The sandbox settings and timings

The spike sandbox ran a warm container per run: `docker run -d` once, then
`docker exec` for each command. Its flags:

- `--network none`, `--read-only`, and a `/tmp` tmpfs of 256 MiB.
- `--user 1000:1000`, `--cap-drop ALL`, `--security-opt no-new-privileges`.
- 1 CPU, 1 GiB memory with swap equal to memory, 256 pids.
- `timeout -s KILL` on each command, and output capped at 12 KB.

Measured: a cold start took 0.35 s (median of 3). A warm exec of `true` took
0.116 s, and `python -c pass` took 0.130 s (median of 10).

The probes in `bench_out.txt` and `bench_out2.txt`:

| Probe | Result |
|---|---|
| Write to `/usr` and `/etc` | Refused, read-only file system |
| Fill `/tmp` | Stopped at the 256 MiB cap |
| Host paths `/c`, `/host`, `/run/desktop` | Absent |
| Network and DNS | No interface, no name resolution |
| A command past its timeout | Killed with exit 137 at 3.2 s |
| A fork bomb | Stopped at 250 processes |
| Memory past 1 GiB | Killed with exit 137 |
| 5 000 000 bytes of output | Cut to head and tail, total reported |
| Capabilities | All zero, `NoNewPrivs: 1` |
| Allowlist: pip install from PyPI | Passed in 2.58 s |
| Allowlist: `example.com` | Refused with 403 |
| Allowlist: a raw socket to `1.1.1.1:443` | Network unreachable |

**One failure to design for.** After the fork bomb, orphan processes stayed.
The next exec failed with `procReady not received`. An exec 9 s later passed.
§7.1 rule 9 is the answer.

**One difference from production.** Docker Desktop on the dev box refused
host bind mounts, so the spike used a named volume and read files through the
container. Production is Linux, and WS-43 mounts the tenant dir. WS-43c must
prove the bind mount on Linux in CI.

### 5.3 The Copilot fixed cost

`copilot_overhead.py` measured the Copilot SDK with no model call. Each
`code_task` call pays `client.start`, which took 9 s cold and 1 s warm. Then it
pays `stop`, which took 2 s. The Copilot T1 run is
`t1-copilot-gemini-flash-latest`. It recorded `client_start_s` 9.07 and
`stop_s` 2.06. Its wall time was 71.2 s.

### 5.4 The model decides speed and pass or fail

On the tasks with no package install (T1, T2a, T2b, T4), the model took 85 to
97 percent of the wall time. On T1:

| Model | Result | Wall time |
|---|---|---|
| `gemini-flash-lite-latest` | Pass | 22.1 s |
| `gemini-flash-latest` | Pass | 87.0 s |
| `gemini-2.5-pro` | Pass | 71.0 s |
| `gemini-2.5-flash` | **Fail**: empty answer after 1 tool call | 5.9 s |

So the model changed the speed by 4 times, and it decided pass or fail.

The spike does not show that MAF is faster than Copilot per task. On T1 with
`gemini-flash-latest`:

| Engine | Wall time | Model or turn time | Input tokens |
|---|---|---|---|
| Copilot SDK | 71.2 s | 60.1 s for the turn | 201 985 |
| MAF, todo on | 87.0 s | 84.0 s of model calls | 271 587 |
| MAF, todo off | 84.3 s | 80.7 s of model calls | 205 919 |

That is why WS-43a and WS-43h measure parity through the Router before any
switch.

### 5.5 Hyperlight was rejected

`hyperlight_probe.py` and `hyperlight_probe2.py` tested the MAF Hyperlight
sandbox. It runs WASI Python only. It has no `csv`, `datetime`, `decimal`,
`pathlib` or `subprocess` module, no timeout and no shell. A coding agent
needs all of them, so WS-43 uses Docker.

### 5.6 MAF quirks that WS-43 must handle

1. **A silent empty answer.** On a malformed tool call, `gemini-2.5-flash`
   returned no text and no error (T1, 5.9 s). WS-43e adds a retry middleware.
2. **The Gemini 3 thought signature.** Gemini 3 sends
   `tool_calls[i].extra_content.google.thought_signature` and refuses the next
   request with HTTP 400 when it is missing. MAF's client drops the field. The
   spike put it back with an httpx transport (`gemini_shim.py`).
3. **Compaction text in `response.text`.** In T2a, `response.text` held
   `[Tool results: …]` lines. In `t1-pro25`, it held the narration of every
   turn and a second answer.
4. **The todo provider adds round trips.** With it off, T1 used 205 919
   input tokens. With it on, T1 used 271 587.
5. **The skills provider caches, and it gives a host path.** The spike set
   `disable_caching=True` so that a new skill lists on the next turn. A skill
   script path is a host path, and the sandbox needs the `/workspace` path.

## 6. Scope and non-goals

### 6.1 In scope

1. The sandbox broker: one module that owns the Docker socket and the
   container lifecycle (§7.1).
2. A curated image, pinned by an immutable reference (§7.2).
3. Egress: no network by default, and an approval-gated allowlist proxy (§7.3).
4. The tools: `run_command`, the MAF file tools over the tenant store, skills
   creation and loading, and `request_network_access` (§7.4).
5. `code_task` as a MAF harness session (§7.5).
6. app-builder on the MAF harness (§7.6).
7. A model eval first, then a parity eval, then the rollout record (§8).
8. The retirement of the Copilot `code_task` path, after parity and an owner
   decision (WS-43i).

### 6.2 Non-goals

- **P5-c, P5-d, P5-b.3 and the pooled cutover.** D83 keeps them parked.
- **`agent-task-manager` and `agent-apis-config`.** A separate PR moves them
  to MAF. It is in flight on the branch `maf-task-apis`.
- **`run_script`.** It stays on the host. Its scripts get the credentials of
  the agent's declared integrations, and they need the network to use them.
  §13 Q1 holds the question.
- **The root `metorite` agent, `mutation.py` and the self-anneal path.**
  They do not change. H-211 records their permission defect.
- **MCP servers in the coding session.** D7 and WS-8c own MCP on MAF. The
  Copilot `code_task` session gets no MCP server today either.
- **Live streaming of the inner steps** of `code_task` to the chat. The
  session returns one report, as it does today.
- **A gateway with more than one worker process.** The broker keeps its
  registry in memory, as `copilot_sandbox.py` does. A second process needs the
  registry in Redis, through the tenant-prefix wrapper (R5c).
- **A UI for grants or egress logs.**
- **gVisor or rootless Docker.** §9 R-1 names them as later hardening.
- **Edits to an agent's built-in repo skills.** A tenant dir holds no code
  (§21.15 rule 6). The mutation path keeps that job.

## 7. Design

### 7.1 The sandbox broker

**What it is.** `apps/services/orchestrator/orchestrator/sandbox_broker.py`.
It is the one place that runs the `docker` CLI for a sandbox. A tool asks the
broker for a container and for an exec, and never calls `docker` itself.

**Its interface:**

| Call | What it does |
|---|---|
| `acquire()` | Returns the container for the bound run, and starts it when needed |
| `exec(handle, command, timeout_s)` | Runs one command and returns an `ExecResult` |
| `grant_egress(handle, reason)` | Opens the allowlist for the container (WS-43f) |
| `release(handle)` | Marks the container idle |
| `covers(agent)` | True when `MAF_CODING_SCOPE` covers the agent and the broker is healthy |
| `sweep()` | Removes every labelled container. The gateway calls it at startup |

`acquire()` takes no organization argument. It reads the run binding itself.

**Rules:**

1. **One container per (organization, agent, thread).** The name is
   `mtr-sbx-` plus the first 16 hex digits of a SHA-256 of the three values.
2. **The tenant comes from the run binding, never from input (R5).** The
   broker reads the organization from `_current_run_org()` and the workspace
   from `artifact_context()["workspace_root"]`. With no organization, it
   raises `SandboxRefused` and starts nothing.
3. **Labels.** Each container carries `metorite.sandbox=1`,
   `metorite.org=<org>`, `metorite.agent=<slug>` and `metorite.thread=<hash>`.
4. **Reuse only on a full match.** The broker reuses a container only when
   its org label, agent label and mount source match the bound run. The
   thread id is client input (§21.15 rule 7). So a thread id alone never
   lends another organization's container.
5. **One mount.** The broker bind-mounts the run's workspace at `/workspace`,
   read-write, and nothing else. The real path must lie strictly under
   `state_root()`, or under the Custom Apps root for app-builder. A path with
   a symlink part is refused. WS-43g adds two read-only mounts for app-builder
   only, from a list in code (§7.6).
6. **The container flags.**
   - `--network none` (WS-43f changes this only after an approval).
   - `--read-only`, plus `--tmpfs /tmp:rw,nosuid,nodev,size=256m`.
   - `--user <uid>:<gid>` of the gateway process. The broker refuses uid 0.
     The files on the bind mount then keep the gateway user as owner, so no
     `--cap-add` is needed.
   - `--cap-drop ALL`, `--security-opt no-new-privileges`, `--init`.
   - `--cpus 1`, `--memory 1g`, `--memory-swap 1g`, `--pids-limit 256`.
   - No `-p`, no `--privileged`, no Docker socket mount, no `--env` that holds
     a secret.
   - Environment: `HOME=/tmp`, `PYTHONUSERBASE=/workspace/.local`,
     `PIP_USER=1`, `PIP_CACHE_DIR=/tmp/pip-cache`, and `PATH` with
     `/workspace/.local/bin` first.
7. **Limits from settings.** Each value in rule 6 comes from a setting in
   `acb_common/settings.py`, with the defaults above.
8. **Caps.** At most `sandbox_max_per_org` containers live for one
   organization (default 2), and `sandbox_max_total` for the box (default 4).
   At a cap, the broker stops the oldest idle container of that organization.
   If none is idle, it raises `SandboxBusy`, and the tool answers with a clear
   error.
9. **Exec hygiene.**
   - Each exec runs under `bash -o pipefail`, so a pipe keeps the exit code of
     the command that failed.
   - Each exec runs in its own session (`setsid`) under `timeout -s KILL`.
   - After each exec, the broker kills every process of that session. An
     exec leaves no orphan.
   - Output is capped at 12 KB: the first 6 KB and the last 6 KB, a marker,
     and the total byte count.
   - One exec at a time per container. A second call waits for the first.
10. **Restart a broken container.** If an exec fails with an OCI error, or the
    container is not running, the broker restarts it once. The mount and the
    network state stay the same. The broker does not run the command again.
    It tells the model that the sandbox restarted, that `/tmp` is empty, and
    that it can run the command again.
11. **Reaping.** A background task runs every 60 s. It stops a container that
    is idle for `sandbox_idle_ttl_seconds` (default 600), or older than
    `sandbox_max_lifetime_seconds` (default 7200). The gateway starts the task
    only when `MAF_CODING_SCOPE` is not empty.
12. **Startup sweep.** A gateway restart ends every run. So at startup the
    broker removes every container labelled `metorite.sandbox=1`, the same way
    `copilot_sandbox.sweep_orphaned_sandboxes` does today.
13. **No fallback to the host.** If the broker cannot start a container, the
    tool fails with a clear error. It never runs the command on the host, and
    `code_task` never falls back to the Copilot path in that case. This is the
    opposite of `copilot_sandbox.py`, which falls back to the host on any
    failure.

### 7.2 The image

**What it is.** `apps/services/orchestrator/Dockerfile.coding-sandbox`. It
holds the tools a coding agent needs, and no secret, no SDK and no CLI.

- The base is `python:3.12-slim-bookworm`, pinned by digest (`@sha256:`).
- System packages: `git`, `bash`, `procps` and `ca-certificates`.
- Node.js LTS from a release tarball that the build checks against a pinned
  SHA-256. app-builder needs it (§4.3).
- Python packages from
  `apps/services/orchestrator/sandbox/requirements.txt`, installed with
  `--require-hashes`. The first set: `pandas`, `numpy`, `openpyxl`,
  `matplotlib`, `pypdf`, `fpdf2`, `markdown`, `tabulate`, `pyyaml`,
  `python-dateutil` and `requests`.
- The image must run as any non-root uid. It holds nothing that only uid
  1000 can read.

**Pinning.** The broker runs the image only by an immutable reference: a
registry digest (`name@sha256:…`) or a local image ID (`sha256:…`). It refuses
a tag such as `:latest`. `sandbox_image` holds the reference.

**User installs.** `pip install --user` writes to `/workspace/.local`. After
an exec that ran `pip` or `npm`, the broker measures `/workspace/.local`. Past
`sandbox_local_quota_mb` (default 512), the next install fails with a clear
message. This is a measured check, not a kernel quota. The blob-store sweep
skips `.local/`, because it is a cache.

### 7.3 Egress

**The default is no network.** A new container has `--network none`. Most
coding tasks need no network. T3a showed that the model then says the task
failed, and does not fake a result.

**The door is one tool, and a person opens it.** `request_network_access`
(WS-43f) runs these steps:

1. Read `sandbox_egress_enabled`. If it is off, answer "network access is off
   on this platform" and stop.
2. Call `decide()` with a network request that names the allowlist, so the
   permission log records it.
3. Call `request_confirmation` with the reason and the domain list, and with
   `non_interactive_default="deny"`.
4. On a refusal, or with no person to ask, answer "not approved" and stop.
5. On approval, call `broker.grant_egress()`.

A run with no chat, such as a workflow or a background job, can never open the
network. `request_confirmation` (`ask_tools.py:345`) already fails closed
there. The card waits at most `sandbox_approval_timeout_seconds` (default
300), then counts as a refusal.

**The grant.**

- The broker mints a random grant token. The token maps to the organization,
  the run, the agent, the domain list and an expiry. The expiry is the end of
  the run or 30 minutes, whichever comes first.
- The broker restarts the container on the organization's own `--internal`
  network, `mtr-egress-` plus a hash of the organization. The workspace stays,
  because it is a bind mount. `/tmp` is cleared. T3b measured the restart at
  1.27 s.
- `HTTP_PROXY` and `HTTPS_PROXY` in the container point at the proxy and carry
  the grant token.
- Two organizations never share an egress network. So one tenant's container
  cannot reach another tenant's container.
- When the grant expires, the next exec restarts the container on
  `--network none`.

**The proxy.**

- One container, `mtr-egress-proxy`, from the pinned image. It runs
  `apps/services/orchestrator/sandbox/egress_proxy.py`, as uid 65534, with
  `--read-only`, `--cap-drop ALL`, 128 MiB and 64 pids (the spike's flags).
- It is on the default bridge and on each organization's egress network. It
  holds no credential.
- It reads the grants from a file that only the broker writes. The file lies
  outside every sandbox mount, and the proxy mounts it read-only.

**What the proxy refuses:**

1. A request with no valid grant token (HTTP 407).
2. A host that is not on the grant's domain list. A domain matches exactly or
   as a suffix, so `pythonhosted.org` allows `files.pythonhosted.org`.
3. A port other than 80 and 443.
4. A host whose resolved addresses include any private, loopback, link-local,
   metadata or reserved address. That covers 10/8, 172.16/12, 192.168/16,
   127/8, 169.254/16, 100.64/10, 0/8, multicast, `::1`, `fc00::/7`,
   `fe80::/10`, and the IPv4-mapped forms of these.
5. A second DNS answer. The proxy resolves once, checks the addresses, and
   connects to the address it checked. A DNS rebind cannot change the target.

**What the proxy logs.** One JSON line per request, allowed or refused. The
line holds these fields: time, organization, run, agent, method, host, port,
address, decision, reason and byte counts.

**The allowlist.** `sandbox_egress_allow_domains`, default
`pypi.org,pythonhosted.org`. The production list reaches third parties, so it
is an owner decision (§12 G-4).

### 7.4 The tools

**`run_command(command, timeout_s=60)`.** It lives in
`packages/acb_skills/acb_skills/sandbox_tools.py`.

1. It reads the run binding. With no run, it fails closed.
2. It calls `decide()` with `full_command_text`. WS-43d adds `run_command` to
   `permission_policy._TOOL_CONTEXT_BUILDERS`, the way `run_script` is mapped
   today. The dangerous-command denylist then applies before the exec.
3. It calls `broker.exec()`. The timeout is at most 300 s.
4. It sweeps changed files under `outputs/`, `agent-data/` and `skills/` into
   the blob store, with the existing `_sweep_to_blob_store`. It skips
   `.local/`.
5. It returns the exit code, the time, the capped output and the sweep count.

**`run_command` is never a platform tool for every agent.** It is not in
`_CORE_STANDARD_TOOL_NAMES`, and `_inject_agent_tools` never injects it. An
agent with no `tool_scope` gets the whole injected surface (the fail-open
branch, `agent_platform_hardening_2026-07.md` §1.1). So a platform-wide
`run_command` would reach every unscoped agent. Only the `code_task` session
and the app-builder factory build it. `manifest.SHELL_TOOLS` gains
`run_command`, so an agent that holds it derives T2.

**The file tools.** `FileAccessProvider` over `TenantFileStore`, a subclass of
MAF's `FileSystemAgentFileStore`, in
`packages/acb_skills/acb_skills/tenant_file_store.py`.

- Its root is the run's workspace, the same dir that the broker mounts.
- The host reads and writes the files directly. The parent class resolves
  each path under the root and refuses a symlink. So a symlink that a command
  plants in the container cannot lead a file tool out of the workspace.
- Each write and each delete also goes to the blob store, through
  `write_artifact.mirror_to_blob_store`, with the run's store key.
- The `code_task` session turns off the approval prompts of the file tools.
  There is no person in a one-shot session, and the store is the boundary.

**Skills.** `SkillsProvider` over a `FileSkillsSource` on
`<workspace>/skills/`.

- Caching is off, so a skill written in this session lists on the next turn.
- A skill script runs in the sandbox. The script runner maps the host path to
  the `/workspace` path, and it refuses a script outside `skills/`.
- The skill format is MAF's: `skills/<name>/SKILL.md` with `name` and
  `description` front matter, and scripts under `skills/<name>/scripts/`.
  T2a and T2b proved the format.

**`request_network_access(reason)`.** §7.3. WS-43d ships a stub that answers
"network access is off on this platform". WS-43f makes it live.

### 7.5 `code_task` as a MAF harness session

`code_session.run_maf_code_session(task, workspace, model)` replaces
`run_copilot_code_session` when `code_task` is in `MAF_CODING_SCOPE`.
`code_tools.code_task` reads the scope on each call.

- **The client.** `OpenAIChatCompletionClient` on the gateway `/v1`, with
  `attributed_openai`. The headers carry `X-CC-Agent` of the calling agent and
  `X-CC-Source: code_task`. So the call goes through the Router with the
  operator's tier bindings (D56), and the Router passes credentials per call
  (D58). No key enters the container.
- **The model.** `tier-balanced`, as today. WS-43a may name a different tier
  under D-AI-4. The owner may overrule that choice.
- **The agent.** `create_harness_agent` with `run_command`,
  `request_network_access`, the file tools over `TenantFileStore`, and the
  skills provider. It has `disable_todo=True`, `disable_mode=True` and
  `disable_web_search=True`. The instructions are today's
  `_HARNESS_INSTRUCTIONS` with three changes. They name the tools. They say
  that installs need an approved network request and go to
  `/workspace/.local`. They drop the git commit step when the workspace is not
  a git repo.
- **The budget.** `CODE_SESSION_TIMEOUT_SECONDS` (600 s), as today. The wait
  for a network approval card counts against it.
- **The report.** The text of the last assistant message, and never
  `response.text`. That stops the compaction leak (§5.6 item 3).
- **Middleware.**
  1. Empty-answer retry. If the final answer has no text and no tool call,
     ask again once with a short nudge. If it is empty again, return an
     error.
  2. If WS-43a finds that the Router drops the Gemini 3 thought signature,
     fix it in the Router adapter, as H-179 did for `reasoning_details`. Never
     build a second client.
- **After the session.** The existing sweep and the existing commit
  fail-safe in `code_tools.code_task` run unchanged.
- **The run context.** The session runs inside the run's own artifact context
  (`enter_artifact_context` and `reset_artifact_context`), as
  `run_copilot_code_session` does today. So the broker reads the right
  tenant.

### 7.6 app-builder on the MAF harness

When `app_builder` is in `MAF_CODING_SCOPE`, `agent-app-builder/agents.py`
builds a MAF harness agent in place of the `GitHubCopilotAgent`.

- Its tools: `run_command`, the file tools over the session workspace, and
  its scoped tools `ask_questions` and `load_design_system`.
- Its container key is (organization, `app-builder`, thread). So one chat
  keeps one container, which replaces the sticky Copilot container.
- The broker adds two read-only mounts for app-builder only: the agent's own
  clone at the same host path, for `build_t2.mjs`, and `t2_vendor_dir()` at
  `/opt/t2-vendor`. It sets `CUSTOM_APPS_T2_VENDOR_DIR=/opt/t2-vendor`. The
  list of extra mounts lives in the broker's code, keyed by agent slug. No
  agent can ask for a mount.
- `_maybe_sandbox_session_workspace` does not run on the MAF path.
- The loader builds agents at startup. So a change of the scope takes effect
  after a gateway restart.
- One defect is inherited and not fixed here. §21.15 records that a
  session-override run writes blob rows with `instance=''`.

## 8. Rollout

1. **Eval first (WS-43a).** Measure both engines and two or more tiers on one
   task set, through the Router, on a local dev stack. This picks the tier
   for `code_task` and sets the baseline.
2. **Build dark (WS-43b to WS-43g).** Every slice merges with
   `MAF_CODING_SCOPE` empty and `sandbox_egress_enabled` off. Production
   behaviour does not change.
3. **Parity (WS-43h).** Run the same task set on both engines, through the
   real broker. MAF reaches parity when all three hold:
   - its pass count is at least the Copilot pass count, at the same tier.
   - no escape probe and no key-leak check fails.
   - its median wall time is at most 1.1 times the Copilot median.
4. **Switch, in this order.** Each step is a flag on the production box, so
   each is an owner act (§12):
   - `MAF_CODING_SCOPE=code_task`. Every agent with injected platform tools
     gets the new engine, because `code_task` is in the core floor (§4.1).
     That includes the agents that still run on Copilot.
   - `MAF_CODING_SCOPE=code_task,app_builder`. app-builder moves.
   - `SANDBOX_EGRESS_ENABLED=1`, with the allowlist the owner names.
5. **Retire (WS-43i).** After 14 days on production with no regression, and
   with the owner's yes, remove the Copilot `code_task` path and
   `copilot_sandbox.py`.

**Which agents do not move here.** `agent-task-manager` and
`agent-apis-config` move in their own PR. The root `metorite` agent, the
external `agent-sales-assistant` and `mutation.py` stay as they are.

## 9. Security review points

**R-1. The kernel is shared.** A Docker container with `runc` shares the host
kernel. A kernel exploit in code that the model wrote would reach a box that
holds every tenant's data. WS-43 lowers the odds with these controls:

- no network, a non-root uid, no capabilities and `no-new-privileges`.
- Docker's default seccomp profile and a read-only root file system.
- CPU, memory and process limits, and no key inside.

The later hardening is gVisor (`runsc`) through a `sandbox_runtime` setting,
or rootless Docker. To install either on the box is an owner act (§12 G-5).

**R-2. The Docker socket is root on the host.** The broker needs the gateway
user in the `docker` group. Then a code execution bug in the gateway becomes
root on the box. `copilot_sandbox.py` and `mutation.py` need the same access
today when they are on. WS-43 limits the risk in three ways:

- One module runs `docker` (fence F1). The list of legacy callers shrinks to
  zero at WS-43i.
- No container mounts the socket (fence F2).
- The later hardening moves the broker into its own systemd unit with a
  narrow local API. Another choice is a socket proxy. It allows only the
  create, exec and remove calls, and only on labelled containers.

To change the box's Docker access is an owner act (§12 G-1).

**R-3. The WS-3a refusal moves to the broker.** WS-3a
(`permissions_sandbox_b6.md` §P5-a.2) refuses a T2 run that
`copilot_sandbox_scope` does not cover. WS-3a is not built (H-213). When it is
built, it reads `sandbox_broker.covers(agent)` in place of
`copilot_sandbox_scope`. A note in §P5-a.2 records the change.

**R-4. H-189 is closed by construction on the MAF path.** `decide()` reads
`path`, and a Copilot SDK write request carries `file_name`. So the
out-of-workspace veto never fires for a Copilot write (H-189).

On the MAF path, every write goes through `TenantFileStore`. The store
resolves the path under the workspace and refuses a symlink. So containment
holds without a request field. H-189 stays open for the Copilot agents until
they move or it gets its own fix.

**R-5. The proxy is the only shared part.** It is the one container on more
than one network. It holds no credential. A grant token opens egress for one
container, to one domain list, until one expiry.

**R-6. Prompt injection.** A hostile document can make the model run a bad
command. The blast radius is that tenant's own workspace. The command has no
network, no key and no other tenant's files.

**R-7. Resource use.** One organization can hold at most 2 containers of
1 GiB each, and the box at most 4. The reaper stops idle containers.

## 10. Fences (R7)

| # | Test file | What breaks it |
|---|---|---|
| F1 | `tests/unit/test_sandbox_broker_seam.py` | A module under `apps/` or `packages/`, outside `sandbox_broker.py`, starts a `docker` process. The legacy list is `copilot_sandbox.py`, `mutation.py` and `evals/coding_engine/`, and WS-43i empties it |
| F2 | `tests/unit/test_sandbox_broker_argv.py` | The `docker run` arguments lose a flag of §7.1 rule 6, gain a `-p`, `--privileged`, `--cap-add` or socket mount, mount more than the workspace, or use uid 0 |
| F3 | `tests/unit/test_sandbox_broker_tenant.py` | The organization comes from input, a run with no tenant starts a container, a container of org A serves org B, or a mount lies outside the allowed roots |
| F4 | `tests/unit/test_sandbox_exec_hygiene.py` | A pipe loses the exit code, a background process outlives its exec, a broken container is not restarted, a timeout does not kill, or output passes the cap |
| F5 | `tests/unit/test_sandbox_egress_proxy.py` | The proxy allows a private, loopback, link-local or metadata address, a host off the list, a request with no token, or a second DNS answer. Or it drops the log line |
| F6 | `tests/unit/test_run_command_tool.py` | `run_command` skips `decide()`, reaches an unscoped agent's injected surface, or leaves `SHELL_TOOLS` |
| F7 | `tests/unit/test_maf_code_session.py` | The scope switch fails, a broker failure falls back to Copilot or to the host, the report uses `response.text`, the empty-answer retry goes, or a write skips the blob store |
| F8 | `tests/unit/test_maf_harness_contract.py` | An `agent-framework-core` upgrade renames a file tool or a `create_harness_agent` parameter that WS-43 uses |
| F9 | `tests/unit/test_sandbox_network_grant.py` | A run with no chat opens the network, a refusal opens it, or an approval does not move the container to its own organization's network |
| F10 | `tests/unit/test_coding_sandbox_image.py` | The base image loses its digest, a requirement loses its hash, or the broker accepts a mutable tag |
| F11 | `tests/unit/test_coding_eval_checkers.py` | An eval checker passes a wrong output or fails a right one |
| F12 | `tests/unit/test_app_builder_engine.py` | app-builder ignores the scope, or asks for a mount outside the broker's list |

F4, F5 (in part), F9 and F10 (in part) need a real Docker daemon. They carry
a new `sandbox_docker` pytest marker, which WS-43b adds to `pyproject.toml`.
The GitHub runners have Docker. ⚠️ A Docker test that skips in CI proves
nothing. So WS-43c adds the `sandbox_docker` suites to the `pr-check.yml`
step that already asserts the R8 suites ran.

**R8.** No slice here adds SQL. The blob mirror reuses
`mirror_to_blob_store`. A test that touches it runs on the dev database
(`bash scripts/dev_db.sh`) as the NOBYPASSRLS app role, with `-rs` and no
skip. If a slice adds a table, R5a and R8 apply to it.

## 11. Slices

Each slice is one PR, and each slice ships dark. A slice heading carries 🔲
until its PR merges.

| Slice | Work | Depends on | Gate |
|---|---|---|---|
| WS-43a | Model eval harness and the first sweep | Nothing | AGENT-SAFE on a local stack |
| WS-43b | The sandbox image | Nothing | AGENT-SAFE. The box build is G-2 |
| WS-43c | The sandbox broker | WS-43b | AGENT-SAFE |
| WS-43d | `run_command`, the file store, skills | WS-43c | AGENT-SAFE |
| WS-43e | `code_task` on a MAF harness session | WS-43d | AGENT-SAFE |
| WS-43f | Egress proxy and the approved grant | WS-43c, WS-43d | AGENT-SAFE. The flip is G-4 |
| WS-43g | app-builder on the MAF harness | WS-43d, WS-43e | AGENT-SAFE |
| WS-43h | Parity eval through the broker | WS-43e, WS-43g | AGENT-SAFE on a local stack. The flips are G-3 |
| WS-43i | Retire the Copilot `code_task` path | WS-43h, 14 days on production | **OWNER-GATE** to merge (G-7) |

### WS-43a — Model eval harness and the first sweep 🔲

**Scope.** A new `evals/coding_engine/` folder: task fixtures, checkers and a
runner. No product code changes.

**Done when:**

1. The task set holds E1 to E8. E1 to E6 are the spike's T1, T2a, T2b, T3a,
   T3b and T4. E7 edits an existing script in place under the
   `agent-data/SCRIPTS.md` contract. E8 builds a small app with a valid
   `index.html`.
2. Each task has a checker that decides pass or fail from files and text
   alone. `tests/unit/test_coding_eval_checkers.py` gives each checker one
   right and one wrong output.
3. The runner takes `--engine copilot|maf` and `--tier`. It writes one JSON
   file per run. The file holds pass, wall time, model time, tool calls,
   tokens, failed execs, approvals and a key-leak flag.
4. The `maf` engine uses an eval-only sandbox under `evals/coding_engine/`.
   Fence F1 lists that folder as a legacy caller, and WS-43i removes it.
5. Every model call goes to the gateway `/v1` of a local dev stack. So each
   call passes the Console Router and the operator's tier bindings.
6. The runner ran E1 to E8 on both engines and on two or more tiers. This
   section of the spec then records the table, the date and the SHA.
7. The run answers one question: does the Router keep
   `extra_content.google.thought_signature` on a Gemini 3 tool call?

**Verification.**

```bash
uv run ruff check evals/coding_engine tests/unit/test_coding_eval_checkers.py
uv run pytest tests/unit/test_coding_eval_checkers.py -q
uv run python -m evals.coding_engine.run --engine maf --tier tier-balanced --tasks all
uv run python -m evals.coding_engine.run --engine copilot --tier tier-balanced --tasks all
```

**Gate.** AGENT-SAFE on a local dev stack. A run against the production Router
is G-6.

### WS-43b — The sandbox image 🔲

**Scope.** `apps/services/orchestrator/Dockerfile.coding-sandbox`,
`apps/services/orchestrator/sandbox/requirements.txt`, the `sandbox_docker`
marker in `pyproject.toml`, and one test file.

**Done when:**

1. The image matches §7.2. It has a base pinned by digest, the system
   packages, a checked Node.js tarball and hashed Python packages.
2. `tests/unit/test_coding_sandbox_image.py` reads the Dockerfile and the
   requirements. It fails on a base with no digest or a line with no hash.
3. A `sandbox_docker` test builds the image, runs it as uid 1000 with
   `--read-only`, and imports each Python package. It also runs
   `node --version` and `git --version`.
4. The image holds no Copilot CLI, no SDK and no secret. The test checks that
   `copilot` is not on `PATH`.

**Verification.**

```bash
uv run pytest tests/unit/test_coding_sandbox_image.py -q -rs
uv run pytest tests/unit/test_coding_sandbox_image.py -q -rs -m sandbox_docker
```

**Gate.** AGENT-SAFE. To build the image on the box is a deploy step (G-2).

### WS-43c — The sandbox broker 🔲

**Scope.** `orchestrator/sandbox_broker.py`, the `sandbox_*` settings in
`acb_common/settings.py`, the startup sweep and the reaper in the gateway
lifespan, the `pr-check.yml` assertion step, and fences F1 to F4.

**Done when:**

1. Each rule of §7.1 holds, and each has a test in F1 to F4.
2. A `sandbox_docker` test bind-mounts a temp dir under a fake `state_root()`
   on Linux. A file that a command writes shows on the host, owned by the
   test's uid.
3. With `MAF_CODING_SCOPE` empty, the gateway starts no reaper, runs no
   sweep and starts no container.
4. `pr-check.yml` fails when the `sandbox_docker` suites report a skip.

**Verification.**

```bash
uv run ruff check .
uv run pytest tests/unit/test_sandbox_broker_seam.py \
  tests/unit/test_sandbox_broker_argv.py \
  tests/unit/test_sandbox_broker_tenant.py -q -rs
uv run pytest tests/unit/test_sandbox_exec_hygiene.py -q -rs -m sandbox_docker
```

**Gate.** AGENT-SAFE. It ships dark.

### WS-43d — `run_command`, the file store and skills 🔲

**Scope.** `acb_skills/sandbox_tools.py`, `acb_skills/tenant_file_store.py`,
the `run_command` entries in `permission_policy.py`, `manifest.py` and
`tool_annotations.py`, and fences F6 and F7 (the store half).

**Done when:**

1. `run_command` matches §7.4: run binding, `decide()` with
   `full_command_text`, a broker exec, the sweep, and a capped result.
2. In enforce mode, a denylisted command is refused before the broker sees
   it.
3. `run_command` is absent from the surface that `_resolve_injected_scope`
   gives an agent with no `tool_scope`.
4. `manifest.SHELL_TOOLS` holds `run_command`, and a manifest that holds it
   derives T2.
5. A `TenantFileStore` write lands in the workspace and in the blob store,
   under the run's store key.
6. A symlink that `run_command` plants, pointing outside the workspace, is
   refused by `file_access_read` and `file_access_write`. This is the H-189
   class of defect, closed by the store.
7. A skill written under `skills/` lists on the next turn. Its script runs
   in the sandbox through the `/workspace` path.
8. `request_network_access` exists as a stub that answers "network access is
   off on this platform".

**Verification.**

```bash
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_run_command_tool.py \
  tests/unit/test_maf_code_session.py \
  tests/unit/test_agent_manifest.py \
  tests/unit/test_permission_policy.py -q -rs
```

**Gate.** AGENT-SAFE. Nothing calls the tools yet.

### WS-43e — `code_task` on a MAF harness session 🔲

**Scope.** `run_maf_code_session` in `code_session.py`, the scope switch in
`code_tools.code_task`, the two middlewares, and fences F7 and F8.

**Done when:**

1. With `code_task` in `MAF_CODING_SCOPE`, `code_task` builds a harness
   session as §7.5 says, and it imports nothing from `copilot`.
2. With the scope empty, `code_task` runs the Copilot path, unchanged.
3. When the broker refuses or fails, `code_task` returns a clear error. It
   does not run the Copilot path, and nothing runs on the host.
4. The report is the last assistant message. A fake client whose
   `response.text` holds `[Tool results: …]` proves it.
5. A fake client that returns one empty answer gets one retry. Two empty
   answers return an error.
6. The session's model calls carry `X-CC-Source: code_task` and the calling
   agent's name.
7. F8 pins the `create_harness_agent` parameters and the eight file tool names
   that WS-43 uses.

**Verification.**

```bash
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_maf_code_session.py \
  tests/unit/test_maf_harness_contract.py \
  tests/unit/test_code_session_sandbox.py -q -rs
uv run pytest tests/unit/test_sandbox_exec_hygiene.py -q -rs -m sandbox_docker
```

**Gate.** AGENT-SAFE. It ships dark.

### WS-43f — Egress: the proxy and the approved grant 🔲

**Scope.** `sandbox/egress_proxy.py`, `broker.grant_egress()`, the
per-organization networks, the grants file, the live
`request_network_access`, the egress settings, and fences F5 and F9.

**Done when:**

1. The proxy refuses each case in §7.3, and F5 tests each one. The address
   cases include IPv6 and the IPv4-mapped forms.
2. The proxy writes one log line per request, with the organization and the
   run.
3. With `sandbox_egress_enabled` off, the tool answers "off", and no proxy
   container or egress network exists.
4. A run with no chat channel is refused, and its container stays on
   `--network none`.
5. An approval moves the container to its own organization's network, and
   pip installs a package through the proxy. A refusal changes nothing.
6. A grant expires at the end of the run, or after 30 minutes. The next exec
   then runs on `--network none`.

**Verification.**

```bash
uv run pytest tests/unit/test_sandbox_egress_proxy.py \
  tests/unit/test_sandbox_network_grant.py -q -rs
uv run pytest tests/unit/test_sandbox_egress_proxy.py \
  tests/unit/test_sandbox_network_grant.py -q -rs -m sandbox_docker
```

**Gate.** AGENT-SAFE. It ships dark. The flip and the production allowlist
are G-4.

### WS-43g — app-builder on the MAF harness 🔲

**Scope.** `apps/agents/agent-app-builder/agents.py`, the app-builder mount
list in the broker, and fence F12.

**Done when:**

1. With `app_builder` in the scope, the factory builds a MAF harness agent as
   §7.6 says. With the scope empty, it builds the `GitHubCopilotAgent`,
   unchanged.
2. Eval task E8 passes on the MAF path. `index.html` is valid, and
   `node build_t2.mjs` runs in the container with the vendor cache.
3. `_maybe_sandbox_session_workspace` does not run on the MAF path.
4. The broker refuses any app-builder mount that is not in its list.

**Verification.**

```bash
uv run pytest tests/unit/test_app_builder_engine.py \
  tests/unit/test_app_builder_sandbox.py -q -rs
uv run python -m evals.coding_engine.run --engine maf --tier tier-balanced --tasks E8
```

**Gate.** AGENT-SAFE. It ships dark.

### WS-43h — Parity eval through the broker 🔲

**Scope.** The eval runner's `maf` engine moves from the eval-only sandbox
to the real broker. No product code changes.

**Done when:**

1. E1 to E8 ran 3 times on each engine, at the tier that WS-43a chose. The
   runs used the broker and the Router of a local stack.
2. This section records the table, the date, the SHA and the verdict
   against the parity rule of §8 step 3.
3. At parity, the PR gives the owner the flips G-3 and G-4, in the order of
   §8 step 4. Short of parity, the PR names what failed.

**Verification.**

```bash
uv run python -m evals.coding_engine.run --engine maf --tier <chosen> --tasks all --repeat 3
uv run python -m evals.coding_engine.run --engine copilot --tier <chosen> --tasks all --repeat 3
```

**Gate.** AGENT-SAFE on a local stack. The flips are G-3 and G-4.

### WS-43i — Retire the Copilot `code_task` path 🔲

**Scope.** Remove `run_copilot_code_session`, `copilot_sandbox.py`,
`Dockerfile.copilot-sandbox`, the `copilot_sandbox_*` settings,
`_maybe_sandbox_session_workspace` and the Copilot branch of app-builder.
Empty the F1 legacy list, except for `mutation.py`.

**Done when:**

1. `MAF_CODING_SCOPE=code_task,app_builder` has run on production for 14
   days with no regression.
2. The owner says yes.
3. F1's legacy list holds `mutation.py` only.
4. The full unit suite passes.

**Gate.** **OWNER-GATE** to merge (G-7). It removes the fallback.

## 12. Owner gates

An agent refuses each of these by name. `work_plan.md` §6.1 registers them.

| # | Act |
|---|---|
| G-1 | Change Docker access on the box, such as adding the gateway user to the `docker` group |
| G-2 | Build or load the sandbox image on the box, or write the deploy step under `deploy/` |
| G-3 | Set `MAF_CODING_SCOPE` on production |
| G-4 | Set `SANDBOX_EGRESS_ENABLED` on production, or set the production allowlist |
| G-5 | Install gVisor or rootless Docker on the box |
| G-6 | Run the eval against the production Router |
| G-7 | Merge WS-43i |
| G-8 | Un-park anything else that D16 parks: P5-c, P5-d, P5-b.3, or T2 for the pooled cutover |

## 13. Open questions

| # | Question | Default until the owner answers |
|---|---|---|
| Q1 | Does `run_script` move into the sandbox? Its scripts need credentials and the network | No. It stays on the host |
| Q2 | Which domains go on the production allowlist beyond PyPI? | `pypi.org` and `pythonhosted.org` only |
| Q3 | Do the caps (2 per organization, 4 per box) fit the production box? | WS-43c measures the box's memory and states it |
| Q4 | Do egress logs need a tenant-scoped table and a UI? | No. Log lines only |
| Q5 | Which tier does `code_task` use? | `tier-balanced`, unless WS-43a shows another tier is better |

## 14. Side findings

The spec work found three defects outside WS-43. `HANDOFF.md` carries each
one with a Check:

- **H-211.** The root `metorite` agent (`agents.py:102-122`) and the external
  `agent-sales-assistant` set `PermissionHandler.approve_all`, so they bypass
  the B6 policy.
- **H-212.** `agent-sales-assistant` imports `copilot.types`, which SDK
  1.0.11 removed. Its factory raises `ImportError`.
- **H-213.** The WS-3 board row says WS-3a and WS-3b shipped. The code has no
  `IsolationTierUnavailable`, no `ISOLATION_TIER_ENFORCE`, no `--read-only`
  and no `--network`.
