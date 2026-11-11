# Orchestration playbook — the know-how that Metorite ships to every organization that connects

**Status: ACTIVE (PB-0, 2026-11-06). KIT-1 (the kit core, task 2549) and KIT-2b (the Codex pack, task 2550) are built. KIT-2c (the Copilot pack, task 2551) and KIT-2d (the Gemini CLI and Antigravity packs, task 2552) are built too. The rest is not.** The decision is **D186** in `work_plan.md` §3, and task 1906 (SW-P10) planned this spec. Its slices are PB-1 to PB-10, and the kit slices KIT-0 to KIT-9 (§6a, epic 2547). Each has a task on the board.

**Board row:** **WS-MCP** in `work_plan.md` §2.1. **Owner direction (2026-11-06):** Metorite "not only becomes a place we use for orchestrating multiple agents". It "also contains the know-how and understanding in the system prompt to help set up multi-org orchestration systems for whoever connects the Metorite MCP." **Price:** part of the flat seat (`launch_surface.md` §4). No SKU and no add-on (D186.3).

**Single owner.** This file owns the content of the playbook, where it lives and how it is layered. `agent_coordination.md` owns the mechanisms it uses: resources (SW-34a), guidance (SW-31), the protocol block (SW-56), notices (SW-64) and the policy store (SW-94). `external_agent_access.md` owns the MCP door and its prompts (§9.6, EA3-39). This file adds no second copy of them.

---

## 1. The answer in one screen

- **The server instructions cannot hold the know-how.** Claude Code cuts them at 4,096 characters, and ours are already over (task 1803). They keep the safety rules only.
- **The know-how lives in six places.** MCP prompts, MCP resources, short tips in tool results, guidance layers, setup tools and an orchestration doctor. Chat reaches the same six (§5).
- **The default layer is neutral.** HathiLabs rules are the HathiLabs layer in the HathiLabs organization, never the product default (§4).
- **One agent is the default.** The setup tools propose one lane first. A second lane needs a named bottleneck (§3).
- **A fact that spreads needs a source and an age.** One false fact at the hub reaches every agent (§3).

---

## 2. Scope and non-goals

### 2.1 In scope

- The content of the playbook (§6) and its versions.
- The six places it lives (§5) and the layering rule (§4).
- The slices PB-1 to PB-10 (§7).

### 2.2 Non-goals

- **A runtime.** The playbook is text and a few read tools. The runner, the notices and the lanes are in `agent_coordination.md`.
- **A policy that grants.** A playbook line never changes a scope, a tool or a rule of the server (D140.2). It advises.
- **A push into the customer's repository.** The GitHub App has no Contents permission (G-9). The starter kit is text that the customer's own agent writes.
- **A claim about the article's numbers.** They are not checked.
- **A new pane or app.** Every surface lives in Projects, Settings or chat (`launch_surface.md` §2).

---

## 3. What the playbook teaches

The patterns come from the article https://niteagent.com/blog/multi-agent-production-2026/, read by the coordinator on 2026-11-06. Take the patterns. The article's figures are not checked, and one has an arithmetic error. The playbook quotes no figure of the article.

| # | Lesson | What the playbook says | Where Metorite helps |
|---|---|---|---|
| 1 | **One agent is the default.** | Add an agent only for a named bottleneck. Parallel lanes fit independent apps. Inside one slice, use a pipeline with an evaluator at each stage: build, verify, review | A lane needs a `purpose` (SW-67). The setup tool proposes one lane (PB-6). The doctor flags a lane with no purpose (PB-7) |
| 2 | **The sub-agent contract.** | A sub-agent gets its own system prompt, a structured brief (objective, output, tools, boundaries), and returns a summary and never a transcript | The brief is a schema on the task (SW-70). The handoff note is the summary (SW-24). `write_brief` is a prompt (PB-2) |
| 3 | **One false fact spreads.** | Give each fact its source and its age. Make a lesson or a guidance block need an approval before it spreads. Keep the verifier re-deriving facts from the code | Lessons with provenance and age (SW-98). Guidance with history (SW-31). The verifier role (SW-59) |
| 4 | **Peers work through a shared artifact.** | Use phase gates and an arbiter. The task is the shared artifact. The owner, a lead or a preview is the arbiter | The stages (SW-71). Requests through the task (SW-65). Previews go to the lead (SW-46) |
| 5 | **Orchestration multiplies cost, and loops are real.** | Put a budget and a loop guard in before scale. Measure the cost of your own setup | Budgets and loop guards (SW-95, SW-38). Cost reads "unknown" until it is measured (SW-20) |
| 6 | **Failures are mostly structural.** | Name the cause from a fixed list, then fix the structure and not the prompt | The cause vocabulary (SW-99). The retro prompt (PB-2) |

Four more topics complete the library. The **brief contract** describes a task that a stranger could do. The **gates** say what must be true before each stage. The **cost model** says how to count, with no invented number. The **lane templates** give a starting lane for a team of one, a team of five, and a team with a release train.

---

## 4. Layers, and the rule that keeps HathiLabs out of the default

Guidance comes in layers (D140.6, SW-31). P10 adds one at the bottom. The nearest layer wins.

| Layer | Who writes it | Holds |
|---|---|---|
| Project | A project lead | Rules for one app |
| Space | A lead of the space | Rules for a group of apps |
| Organization | An admin | The organization's own rules |
| **Metorite default** | **Metorite, shipped with the server** | **The playbook defaults and the safety rules** |

**The rule (D186.2).** The Metorite default layer holds no rule that belongs to HathiLabs. These are Simplified Technical English, the R rules (R1 to R8), D numbers and the gate labels of this repository. They also include the names of its hooks and guard files, and the 21:30 UTC release. The product holds none of them either. A release cut-off and a stage list are policy defaults (SW-94), and the fence scans those defaults and the seed data. The three tier names are product vocabulary. Those bind HathiLabs work. They are written in the HathiLabs organization layer, or in the repository's `CLAUDE.md`. They do not copy between the two (`CLAUDE.md` §5).

**An organization may switch the tips of the default layer off. It may not switch off a safety line.** The safety lines are the stopping rule, the one-disposition rule, `ask_on_task` for a question, and `record_outside_decision`.

**The fence.** `tests/unit/test_playbook_org_neutral.py` (new, PB-1 creates it for the playbook files, PB-5 widens it) reads every file of the playbook, every default tip, the runtime strings `_ROUTINE_INSTRUCTIONS` and `_RUN_INSTRUCTIONS` of `agent_edge/mcp.py` (not its comments), the `work_on_task` text, the protocol block of SW-56 and the plugin package (SW-82). It fails on a match with an entry of `tests/fixtures/playbook_org_tokens.json` (new). An entry is a token or a regex. D numbers use a regex such as `\bD\d{2,3}(\.\d+)?\b`. A second test checks that each entry still matches text in `CLAUDE.md`, `work_plan.md` §1 or `docs/style_ste.md`, so the list cannot drift. The fence is a SAFETY fence and joins `tests/fences.json`. It blocks from the PB-1 merge, because D186.2 is an owner call. PB-1 adds the entry to `tests/fences.json`. The inputs also include the shipped policy defaults of SW-94, the default stage list of SW-71 and the seed data.

**The HathiLabs layer in Metorite** (PB-10) is a pointer only: "Follow `CLAUDE.md` in the repository." It does not mirror the rules.

---

## 5. Where the know-how lives

| # | Place | What it holds | Mechanism |
|---|---|---|---|
| 1 | **MCP prompts** (slash commands) | `set_up_agent_team`, `plan_slices`, `write_brief`, `review_my_fleet`, `run_retro`, and the session commands of SW-82 | `prompts/list` (EA3-39) |
| 2 | **MCP resources** | `metorite://playbook/index`, `.../pattern/{slug}`, `.../brief-contract`, `.../gates`, `.../failures`, `.../cost-model`, `.../lane-templates`, `.../kit/{file}`. Each has a version | SW-34a |
| 3 | **Tips in results** | At most 2 short lines in the result of `claim_task`, `my_work` and `delegate_task`. Example on `claim_task`: "Write a brief before you delegate." Each line names a playbook topic and shows once for each run | SW-56 |
| 4 | **Guidance layers** | The Metorite default, then the organization, the space and the project | SW-31 |
| 5 | **Setup tools** | `propose_agent_setup` reads the organization and proposes lanes, leads, gates and budgets on a card. `get_starter_kit` returns the kit files | New tools (PB-6) |
| 6 | **An orchestration doctor** | Checks a setup against the playbook and the board health. Writes Needs attention rows | PB-7 |

**Chat parity (PB-8).** "Set up agents for my team" works from the Metorite chat. The chat uses the same tools. The playbook is a pack of the orchestrator (WS-67), loaded when the intent matches, so it adds nothing to the base prompt.

**A client with no resource support** still reads the playbook through the class A tool `read_playbook(topic)` (PB-3).

**A prompt holds no member data.** `prompts/get` makes no gateway request (EA3-39 P6). A prompt such as `review_my_fleet` tells the agent which tools to call. The agent fetches the data.

---

## 6. The content

Files live in one shared package, `packages/acb_playbook/`, as Markdown with a header: `slug`, `version`, `title`, `summary` and `audience`. The edge and the gateway both read that package, so one source serves the prompts, the resources and the default guidance layer. A change of content is a change of version, with no migration.

- `index`
- `patterns/` one file for each pattern of §3, each with "when to use" and "when not"
- `brief-contract`
- `gates`
- `failures` — the cause vocabulary of SW-99, each cause with its guard
- `cost-model`
- `lane-templates/` templates with a `version` (PB-9)
- `kit/` `AGENTS.md`, hook examples, a verifier agent, a reviewer agent, the plugin guide

**The writing rule.** Plain short sentences, no jargon of this repository, no figure from the article. Each file passes the neutral-layer fence of §4.

---

## 6a. The agent kit

The kit is the static part of the playbook. It is a set of files that a member puts in a repository or a home folder. With these files, an agent of any client knows how to work with Metorite. KIT-0 is this contract (task 2548).

**One core, many packs.** The core is one neutral `AGENTS.md` of at most 150 lines, plus one skill folder. Each client gets a thin pack of the files it needs. Each file of a pack points to the core and holds no second copy of it. Fence: the line count and the pointer test of KIT-1.

**The file map.** KIT-0 checked each cell against the official vendor docs on 2026-11-11, and each cell names its source. The vendors change these names often, so KIT-5 re-checks them.

| Client | Instruction files | Connect step |
|---|---|---|
| Claude Code | `CLAUDE.md`. Claude Code also reads `AGENTS.md` when no `CLAUDE.md` exists (v2.1.277 or later). A `CLAUDE.md` can import it. Source: `https://code.claude.com/docs/en/memory`. The pack of skills, plugin and hooks is SW-82 (task 1927). A plugin has an optional manifest `.claude-plugin/plugin.json` (only `name` is required). Every other part sits at the plugin root: `skills/<name>/SKILL.md`, `agents/*.md`, `hooks/hooks.json` (with a top-level `hooks` key) and `.mcp.json`. A `CLAUDE.md` at the plugin root is not loaded. Source: `https://code.claude.com/docs/en/plugins-reference` | MCP connection with OAuth (MCP-G17). SW-82 owns the steps |
| OpenAI Codex | `AGENTS.md` in the project, and in `~/.codex` for the global level. `AGENTS.override.md` wins in each directory. The limit is `project_doc_max_bytes`, 32 KiB by default. Source: `https://developers.openai.com/codex/guides/agents-md`. Custom prompts in `~/.codex/prompts/` are deprecated and local only, so the kit ships a skill and no prompt file. Source: `https://developers.openai.com/codex/custom-prompts` | Add a table `[mcp_servers.<name>]` with `url` in `~/.codex/config.toml`, or in `.codex/config.toml` of a trusted project. Then run `codex mcp login <name>`. Source: `https://developers.openai.com/codex/mcp` |
| GitHub Copilot | `.github/copilot-instructions.md` for the repository. `AGENTS.md` anywhere in the repository (the nearest file wins). Path files `.github/instructions/NAME.instructions.md`. Source: `https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions` | VS Code: `.vscode/mcp.json` with a top-level `servers` key, and an entry with `"type": "http"` and `url`. A secret goes through `inputs`, never in the file. Source: `https://code.visualstudio.com/docs/agents/reference/mcp-configuration`. Coding agent on GitHub.com: the repository setting Copilot, MCP servers, holds one JSON with a top-level `mcpServers` key. Each server needs `type` and `tools`, and a remote one needs `url`. A secret name must start with `COPILOT_MCP_`. The agent reads `.github/copilot-instructions.md`. The docs imply `AGENTS.md` and do not name the coding agent, so the pack also ships `.github/copilot-instructions.md`. Source: `https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/extend-coding-agent-with-mcp` |
| Gemini CLI | `GEMINI.md` in `~/.gemini` and in the workspace. The setting `context.fileName` adds `AGENTS.md` to the list of names. Source: `https://geminicli.com/docs/cli/gemini-md/` | The object `mcpServers` in `settings.json` (`~/.gemini/settings.json` or `.gemini/settings.json`). A remote entry uses `httpUrl` or `url`, with an optional `oauth` object. Source: `https://geminicli.com/docs/tools/mcp-server/` |
| Google Antigravity | `AGENTS.md` or `GEMINI.md` in the workspace (no frontmatter). `~/.gemini/AGENTS.md` for the global level. Rule files in `.agents/rules/*.md` need a `trigger` frontmatter, or Antigravity drops them without a notice, so the kit uses none. Skills in `.agents/skills/<name>/SKILL.md`. Source: `https://antigravity.google/docs/rules` | `.agents/mcp_config.json` in a workspace, or `~/.gemini/config/mcp_config.json`. A remote entry is `{"mcpServers": {NAME: {"serverUrl": URL}}}` over Streamable HTTP. It does not take `httpUrl`, which Gemini CLI uses. Source: `https://antigravity.google/docs/mcp` |

**Three layers.** The kit is static, neutral and versioned. The MCP holds the live text: prompts, `read_playbook` and resources. Company guidance (SW-31) holds the rules of one organization. The kit never copies the MCP text (PB-10).

**No secret.** The kit holds no secret. A client connects with OAuth or with the box token (MCP-G17). Fence: `test_playbook_org_neutral.py` (extended by KIT-1) and the secret scan of KIT-3.

**The version.** Each kit file holds a `kit_version`, and the MCP reports the same value. A change to a kit file raises it. A client that reads an old value gets a notice from the doctor (KIT-5). Fence: `tests/unit/test_kit_version.py` (new, KIT-5).

**The protocol that the core teaches.** It has at most 12 lines:

1. Call `my_work` first, then `claim_task`.
2. Call `get_task_context`, and call `get_plan` for the order of the slices.
3. Work in a worktree and on a branch. Put the task short id in the branch name.
4. Post activity at least once a minute.
5. At a wait, park the run. Never sleep.
6. End with one disposition: review or done, a question, or a release.
7. The verifier is never the implementer.
8. A sub-agent never posts to Metorite. The coordinator posts for it.

Fence: `tests/unit/test_kit_core.py` (new, KIT-1) counts the lines and checks each tool name against the allowlist.

---

## 6b. Delegating a sub-project, and hand-offs

**Owner directive, 2026-11-10 (D204 points 10 and 11).** The owner gives each app project to one person. That person's agents work along the critical path of that project. This section is the contract between two people and their agents.

**What each project carries.** The description of a project holds a delegation brief with these lines: the owner slot, the entry point (the first ready slice), the critical path (task numbers in order), the hand-ins (work this project needs from others), the hand-outs (work others need from this project), and the first command to run (`my_work`, then `claim_task`). The brief holds no secret. The program plan (task 2546) holds the order of the projects.

**The hand-off form.** When task A in project P gives work that task B in project Q needs, both tasks hold one comment that starts with `Hand-off:`. The comment names the other task number, the other project, the direction (`gives` or `needs`) and the reason in one sentence. A `blocks` link joins A to B. The person who owns Q reads the comment in `my_work` or in the plan of project Q. An agent never starts B while A is open. It posts a question on B instead.

**Who writes a hand-off.** The agent that finds the dependency writes both comments and the link. Another project owner needs no permission to read the comment. A change to the other project's own task goes through a comment, and the owner of that project makes the change.

**Fence.** Advisory until KIT-5. The doctor of KIT-5 lists each `blocks` link that crosses a project and has no `Hand-off:` comment on both ends (test `tests/unit/test_handoff_comments.py`, new).

---

## 6c. The learning loop

**Owner directive, 2026-11-10.** Metorite holds the plan and the orchestration. It also holds the harness of every agent that connects. Each week it reviews the agent sessions and the plan, learns from them, and changes the harness. Any organization can run the loop on its own data. Epic task 2624.

**The weekly cycle has five stages.**
1. **Collect.** Signals come from agent runs, task events, pull requests and CI, the tool failure log and MCP refusals. A signal holds ids and counts and never task text.
2. **Analyze.** Each failed or stopped run gets one cause code. The registry has about 12 codes, among them `spec_gap`, `stale_anchor`, `wrong_seam`, `tool_gap`, `rate_limit`, `env_flake`, `scope_creep`, `verify_miss`, `owner_wait`, `merge_conflict`, `plan_drift` and `unknown`.
3. **Recalibrate.** A read-only health report of the board lists duplicates, stale tasks, "done" tasks with no proof, slices with no estimate, link or epic, and estimate against actual. It proposes. A member approves a list in chat. A bulk tool applies it.
4. **Learn.** A finding becomes a lesson: pattern, cause code, evidence links, fix and its kind, metric and baseline, status. An approved lesson changes the guidance, a kit file, a hook, an agent definition, a fence test, a spec or the code. The kit version rises with each change to a kit file.
5. **Measure.** The next reviews compare the metric over 4 weeks before and 4 weeks after. The verdict is helped, no effect, worse, or too early.

**The rules.**
- The loop proposes and a member approves. It never merges and never approves its own request.
- Data is tenant-scoped (R5). The loop reads only the repositories an org connected.
- Model calls go through the router. Sorting uses the decide tier.
- The default layer holds no HathiLabs text (PB-4).
- The loop ships dark behind `LEARNING_ORGS`.
- Fences: `tests/unit/test_learn_signals_r8.py`, `test_cause_codes.py`, `test_lessons_lifecycle.py`, `test_lesson_promotion.py`, `test_learning_org_neutral.py` (all new, named by their slices). Until then the rules are advisory.

**The settings (LL-12).** The Projects app holds these as switches, per organization and per space: on or off, the day (default Monday), the time and zone (default 07:00 of the org zone), the sources, the recipients, and the limit of fix tasks (default 5). The chat changes them with the same result. The scheduler reads them and hard-codes nothing.

**The guide (LL-13).** One guide teaches the best workflow to people and to agents. The member guide and the help chapters show it in the app. A chapter of the playbook serves it through `read_playbook` for agents. The neutral `AGENTS.md` of the kit points to it and holds no copy (PB-10). The guide explains each setting, the first week (connect, import the board, set the weekly time, run one review by hand, approve the first lesson), and what an agent never does.

**Until LL-3 ships,** a person runs the review by hand each Monday, with the pattern of the replan of 2026-11-09 and 2026-11-10.

---

## 7. The slices

Each names its scope, its done-when, its fence (R7), its risk tier and its gate. All are AGENT-SAFE and ship dark behind `PLAYBOOK_ORGS`, unless they say more. A migration makes a slice HIGH.

#### PB-1 · The playbook content, v1

- **Metorite task.** 1947.
- **Scope.** The files of §6, the header schema, the loader, and a version for each file. It creates `test_playbook_org_neutral.py`, the token fixture for these files, and the entry in `tests/fences.json`.
- **Done when.** The loader lists every file with its version. A file with a bad header fails the test.
- **Fence.** `tests/unit/test_playbook_content.py` (new), `test_playbook_org_neutral.py` (new).
- **Risk.** LOW. **Gate.** AGENT-SAFE.

#### PB-2 · The prompts

- **Metorite task.** 1948.
- **Scope.** `set_up_agent_team`, `plan_slices`, `write_brief`, `review_my_fleet` and `run_retro` in `agent_edge/prompts.py`, and the session commands of SW-82 in the same registry. Each message is fixed text and names the tools to call. A prompt makes no gateway request. This widens EA3-39 item 1 ("one prompt"). Prompts that only read list with `projects:read`. Prompts that write list with `projects:write`. `external_agent_access.md` gets a one-line pointer.
- **Done when.** `prompts/list` holds them by scope. A prompt message holds no member text. The existing fences of `test_mcp_edge_prompts.py` pass, with EA3-39 item 1 and item 8 updated.
- **Fence.** `test_mcp_edge_prompts.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** PB-1.

#### PB-3 · The resources and `read_playbook`

- **Metorite task.** 1949.
- **Scope.** The `metorite://playbook/...` resources of §5 beside the three resources of SW-34a. The tool `read_playbook(topic)` for a client with no resources. Content is the same for every organization. The organization layer adds its notes after the default text, marked as the organization's.
- **Done when.** A client lists and reads each resource. An unknown topic answers not found. The text leaves as `untrusted_text`.
- **Fence.** The transport test of SW-34a, `test_mcp_edge_coverage.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-34a.

#### PB-4 · Tips in the results

- **Metorite task.** 1950.
- **Scope.** `tips` beside `protocol` (SW-56): at most 2 lines of 120 characters, chosen by a rule table on the tool and the context. Each names a topic and shows once for each run. The edge keeps one `SET NX` key in the tenant Redis wrapper for each connection, run and tip, so "once" needs no migration. **Changed on 2026-11-07 (PB-4 build):** the first text said a gateway column `pm_agent_runs.tips_seen`. A tip is a hint and grants nothing. A lost key shows a tip again, and a store outage shows none. So the state does not belong on the lease table. The tips show only while the runs flag is on and the organization is on `playbook_orgs`. The first tips are `brief` (a task with little text, until SW-70 gives the task a brief) and `stalled` (a quiet run in `my_work`). They are separate from the safety lines.
- **Done when.** A claim of a delegated task with no brief carries the brief tip once. The same run sees it once. An organization that switched tips off sees none, and still sees the safety lines.
- **Fence.** `test_mcp_edge_context.py`, `test_mcp_edge_prompts.py`.
- **Risk.** STANDARD (no migration, see above). **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-56.

#### PB-5 · The default layer, and the neutral fence

- **Metorite task.** 1951.
- **Scope.** The Metorite default level in guidance (SW-31): read-only, versioned, below the organization. The setting that switches tips off. It widens the neutral test to the product strings and defaults, and adds the drift test of §4.
- **Done when.** `get_task_context` returns the default block last. A HathiLabs token in any playbook file fails the test. A token in the fixture that is gone from `CLAUDE.md` fails the second test.
- **Fence.** `test_playbook_org_neutral.py` (extended), `test_agent_guidance_r8.py`.
- **Risk.** HIGH (a channel that reaches every agent, tenancy). **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-31.

#### PB-6 · Setup tools and the starter kit

- **Metorite task.** 1952.
- **Scope.** `propose_agent_setup` reads the apps, the leads, the members and the connected repositories by name. It returns one proposal: one lane to start, the leads that are missing, the gates and the budgets. It raises a card, and the admin accepts each part. A lead is a grant write, so the admin accepts it alone. `get_starter_kit` returns the kit files as text.
- **Done when.** R8: a proposal lists only what the caller may see. Nothing is written before the admin accepts. A lead is never set by a proposal. A proposal starts with one lane.
- **Fence.** `test_agent_setup_proposal_r8.py` (new), `test_mcp_edge_coverage.py`.
- **Risk.** HIGH (grant writes, visibility). **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-67, SW-94.

#### PB-7 · The orchestration doctor

- **Metorite task.** 1953.
- **Scope.** A set of pure checks over data. Examples: an app with no lead, no guidance, or no required checks. A lane with no purpose or no budget. A high rate of runs that end with no disposition. No verifier run on HIGH tasks in 30 days. A policy that auto-answers a HIGH task. A lesson past `verify_by`. A proposal queue older than 14 days. It runs each day and on demand. It writes Needs attention rows of kind `orchestration_check` with the fix. It uses no AI credit.
- **Done when.** R8: a fixture organization with 5 faults gets 5 rows. A fixed fault leaves its row. A reader sees only rows of their scope.
- **Fence.** `test_agent_doctor_r8.py` (new), `test_needs_attention_r8.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-44, SW-67, SW-99.

#### PB-8 · Chat parity

- **Metorite task.** 1954.
- **Scope.** Chat tools `propose_agent_setup` (class B, one confirm card), `agent_doctor` (class A) and `explain_pattern` (class A). The playbook pack of the orchestrator.
- **Done when.** "Set up agents for my team" from chat raises the proposal card. `test_projects_chat_coverage.py` passes.
- **Fence.** `test_projects_chat_coverage.py`, the new-chat-tool fence set.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. D145 owner preview. **Depends on.** PB-6, PB-7, WS-67 OA-7b.

#### PB-9 · Versioned lane and prompt templates

- **Metorite task.** 1955.
- **Scope.** A lane template has a version. This slice adds the template-version column to `pm_agent_lanes` (SW-67 does not). An organization copies one to make a lane, and the lane records the version. When a newer version ships, the lane shows "Template update available". The organization's copy never changes by itself.
- **Done when.** R8: a lane records its template version. A new version raises the notice and changes no lane.
- **Fence.** `test_agent_lanes_r8.py` (extended).
- **Risk.** HIGH (a migration: a column on the lane table). **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-67.

#### PB-10 · The HathiLabs layer

- **Metorite task.** 1956.
- **Scope.** In the HathiLabs organization, write the organization guidance: "Follow `CLAUDE.md` in the repository." Nothing more.
- **Done when.** An agent that claims a HathiLabs task gets the pointer in its guidance.
- **Fence.** A test that the organization guidance of the HathiLabs fixture organization holds the pointer and no copy of `CLAUDE.md`. Advisory for the live write (R7).
- **Verify.** `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_guidance_r8.py` and a read of the live guidance after the write.
- **Risk.** LOW. **Gate.** OWNER-GATE, because the write changes the guidance of a live organization, and the owner does it. **Depends on.** SW-31.

#### KIT-0 · The kit contract

- **Metorite task.** 2548.
- **Scope.** §6a: one core, many packs, the file map, the three layers, the version rule and the protocol. The slice rows below. The verified and unverified facts of the file map.
- **Done when.** Each "Verify at build" cell has a source or stays marked. `ste-lint` passes on this file.
- **Fence.** `node .claude/hooks/ste-lint.mjs project-docs/specs/orchestration_playbook.md`. Advisory for the vendor facts, which change (R7).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** PB-1.

#### KIT-1 · The core

- **Metorite task.** 2549.
- **Scope.** One neutral `AGENTS.md` of at most 150 lines, and one skill folder, in `packages/acb_playbook/kit/`. It holds the protocol of §6a and a `kit_version`. It adds `kit_version` to the neutral-layer fence.
- **Done when.** The core is at most 150 lines and holds all eight protocol items. A HathiLabs token in it fails the test.
- **Fence.** `tests/unit/test_kit_core.py` (new), `test_playbook_org_neutral.py` (extended).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-0, PB-1.

#### KIT-2b · The Codex pack

- **Metorite task.** 2550.
- **Scope.** The files for Codex from the file map, and the `config.toml` block with the `codex mcp login` step. Each file points to the core.
- **Done when.** A fixture check shows that each file exists, points to the core and holds no secret.
- **Fence.** `tests/unit/test_kit_packs.py` (new).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-1.

#### KIT-2c · The Copilot pack

- **Metorite task.** 2551.
- **Scope.** `.github/copilot-instructions.md`, the `AGENTS.md` use, and the `.vscode/mcp.json` example. The settings of the coding agent follow the check at build.
- **Done when.** The same fixture check as KIT-2b passes for the Copilot files.
- **Fence.** `tests/unit/test_kit_packs.py` (new).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-1.

#### KIT-2d · The Antigravity and Gemini pack

- **Metorite task.** 2552.
- **Scope.** `GEMINI.md`, the `context.fileName` setting, and the `mcpServers` block for Gemini CLI. The Antigravity files follow the check at build.
- **Done when.** The same fixture check as KIT-2b passes for these files.
- **Fence.** `tests/unit/test_kit_packs.py` (new).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-1.

The Claude pack is SW-82 (task 1927) in `agent_coordination.md`. It follows this contract.

#### KIT-3 · The `metorite-guard` check

- **Metorite task.** 2553.
- **Scope.** The command `metorite-guard`. It checks that the session holds a task, that the branch name and the pull request body name the task, and that the turn ends with one disposition. A harness runs it from its own hook, from a git hook or from CI. It reads the connection config of the kit and holds no secret. The kit build also scans each kit file for a secret and for a copy of MCP text.
- **Done when.** A fixture with each failure prints one sentence for it. A fixture file with a token fails the kit scan. A clean kit passes.
- **Fence.** `tests/unit/test_kit_guard.py` (new).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** KIT-1, SW-57 (task 1889).

#### KIT-4 · The download

- **Metorite task.** 2554.
- **Scope.** A route that serves the pack of one client as a zip. It reads the `kit/` files and adds no content. The route is dark behind `PLAYBOOK_ORGS`.
- **Done when.** A listed organization gets the zip. An unlisted organization gets 404. The zip holds the same `kit_version` as the MCP.
- **Fence.** `tests/unit/test_kit_download_r8.py` (new), `test_tenant_coverage.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE dark. **Depends on.** KIT-1, KIT-2b, KIT-2c, KIT-2d, KIT-6, SW-82.

#### KIT-5 · Versions and the doctor

- **Metorite task.** 2555. It extends PB-7 (task 1953).
- **Scope.** The `kit_version` fence of §6a, and a doctor check that flags a stale kit.
- **Done when.** A change to a kit file with no version change fails the test. A stale kit raises one doctor row.
- **Fence.** `tests/unit/test_kit_version.py` (new), `test_agent_doctor_r8.py` (extended).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** KIT-4, PB-7.

#### KIT-6 · The conformance matrix

- **Metorite task.** 2556. It extends MCP-G23 (task 2523).
- **Scope.** A table of clients against the protocol steps, with the result of a scripted run for each.
- **Done when.** Each client of the file map has one row with a date and a result.
- **Fence.** The suite of MCP-G23, extended. A live client run is advisory (R7).
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** KIT-2b, KIT-2c, KIT-2d, SW-82.

#### KIT-7 · The company layer

- **Metorite task.** 2557. It uses SW-31 (task 1048) and PB-5 (task 1951).
- **Scope.** The path from a kit file to the organization guidance. It uses the default layer of PB-5.
- **Done when.** An agent that reads the kit gets the organization guidance from `get_task_context`, and not from a kit copy.
- **Fence.** `test_agent_guidance_r8.py`, `test_playbook_org_neutral.py`.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-1, SW-31, PB-5.

#### KIT-8 · The docs page

- **Metorite task.** 2558.
- **Scope.** One page that tells a member how to connect each client. It repeats no kit text and links to the download.
- **Done when.** The page lists each client of the file map with its connect step.
- **Fence.** Advisory (R7). A person reads the page against the file map.
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-2b, KIT-2c, KIT-2d, SW-82.

#### KIT-9 · Orchestration recipes and lane templates

- **Metorite task.** 2559. It uses PB-9 (task 1955).
- **Scope.** The patterns of §3 as recipes, and the lane templates of PB-9, as kit files.
- **Done when.** Each recipe names its pattern and each template holds a `version`.
- **Fence.** `tests/unit/test_playbook_content.py`, `test_agent_lanes_r8.py` (extended).
- **Risk.** LOW. **Gate.** AGENT-SAFE. **Depends on.** KIT-1, PB-9.

---

## 7a. The learning loop slices (WS-LL, epic task 2624)

| Slice | Task | Scope | Risk | Depends on |
|---|---|---|---|---|
| LL-0 | 2625 | This section and §6c (docs) | LOW | none |
| LL-1 | 2626 | Signal store, tenant-scoped (`learn_signals`, `learn_weeks`) | HIGH (migration) | LL-0, SC-9 (2576) |
| LL-2 | 2627 | Cause codes and the sorter | STANDARD | LL-1, LL-8 |
| LL-3 | 2628 | The weekly review run | STANDARD | LL-2, LL-12 |
| LL-4 | 2629 | The board health report (read-only) | STANDARD | none |
| LL-5 | 2630 | The lessons register | STANDARD | LL-3 |
| LL-6 | 2631 | Harness promotion to pull requests | HIGH | LL-5 |
| LL-7 | 2632 | Effect measurement | STANDARD | LL-6, LL-1 |
| LL-8 | 2634 | The run reflection field | STANDARD | LL-0, MCP-G28 (2541) |
| LL-9 | 2635 | A My Day card and a chat answer | STANDARD | LL-3, LL-5 |
| LL-10 | 2636 | Connected repositories | HIGH | LL-1, LL-6 |
| LL-11 | 2637 | The offer to every organization | STANDARD | LL-9, LL-10 |
| LL-12 | 2638 | Weekly review settings in Projects | STANDARD | none |
| LL-13 | 2639 | The best-workflow guide | LOW | LL-0, LL-12 |

Each slice ships dark behind `LEARNING_ORGS`. Each slice that adds a rule names its test in its task (R7).

---

## 8. Verify commands

| Slice | Command |
|---|---|
| PB-1 | `uv run pytest -q -p no:cacheprovider tests/unit/test_playbook_content.py tests/unit/test_playbook_org_neutral.py` |
| PB-2 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_prompts.py` |
| PB-3 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_session.py tests/unit/test_mcp_edge_coverage.py` |
| PB-4 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py tests/unit/test_mcp_edge_claim.py tests/unit/test_mcp_edge_prompts.py` |
| PB-5 | `uv run pytest -q -p no:cacheprovider tests/unit/test_playbook_org_neutral.py tests/unit/test_agent_guidance_r8.py` |
| PB-6 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_setup_proposal_r8.py tests/unit/test_mcp_edge_coverage.py` |
| PB-7 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_doctor_r8.py tests/unit/test_needs_attention_r8.py` |
| PB-8 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_chat_coverage.py` |
| PB-9 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_lanes_r8.py` |
| KIT-1 | `uv run pytest -q -p no:cacheprovider tests/unit/test_kit_core.py tests/unit/test_playbook_org_neutral.py` (new file first) |
| KIT-2b to KIT-2d | `uv run pytest -q -p no:cacheprovider tests/unit/test_kit_packs.py` (new) |
| KIT-3 | `uv run pytest -q -p no:cacheprovider tests/unit/test_kit_guard.py` (new) |
| KIT-4 | `uv run pytest -q -p no:cacheprovider tests/unit/test_kit_download_r8.py tests/unit/test_tenant_coverage.py` (new file first) |
| KIT-5 | `uv run pytest -q -p no:cacheprovider tests/unit/test_kit_version.py tests/unit/test_agent_doctor_r8.py` (new file first) |
| PB-0 (this file) | `node .claude/hooks/ste-lint.mjs project-docs/specs/orchestration_playbook.md` and `uv run pytest -q -p no:cacheprovider tests/unit/test_doc_names_resolve.py tests/unit/test_index_completeness.py` |

---

## 9. What this spec could not settle

- **Whether Claude Code reads a resource without an @-mention.** The docs say it provides tools to list and read resources. They do not say the model does so on its own. `read_playbook` covers a client that does not.
- **The fixture of tokens.** It starts with the tokens listed in §4. Add any token that the first review finds.
- **The names of the prompts.** Claude Code shows a prompt as `/mcp__metorite__<name>`. The plugin commands (SW-82) give shorter names.
- **A second language.** The playbook is in English only.

---

## Changelog

- **2026-11-06.** Written for PB-0 (task 1906), after the owner's second direction. Records D186.
- **2026-11-09.** Replan (task 2518). Adds §6a, the agent kit, and the slices KIT-0 to KIT-9 (epic 2547). Section 6a lists the sources of the vendor facts.
- **2026-11-11.** KIT-0 (task 2548). The five open cells of the file map in §6a are settled from official docs, with a source for each.
