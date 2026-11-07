# Orchestration playbook — the know-how that Metorite ships to every organization that connects

**Status: ACTIVE (PB-0, 2026-11-06). Nothing is built.** The decision is **D186** in `work_plan.md` §3. This spec was planned in task 1906 (SW-P10). Its slices are PB-1 to PB-10. Each has a task in Backlog under epic 321.

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
| 2 | **MCP resources** | One resource for each topic: `metorite://playbook/{slug}`, such as `metorite://playbook/brief-contract` and `metorite://playbook/patterns/one-agent-first`. The slug is the path of the topic in `acb_playbook`. Each has a version | SW-34a, PB-3b |
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
- **Scope.** PB-3 is split (orchestrator, 2026-11-07). **PB-3a** is the tool `read_playbook`, built. **PB-3b** is the `metorite://playbook/{slug}` resources of §5, beside the project and task resources of SW-34a, and it needs both flags: `agent_resources_orgs` and `playbook_orgs`. Content is the same for every organization. The organization layer adds its notes after the default text, marked as the organization's.
- **Done when.** A client lists and reads each resource. An unknown topic answers not found. The text leaves as `untrusted_text`.
- **Fence.** The transport test of SW-34a, `test_mcp_edge_coverage.py`.
- **Risk.** STANDARD. **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-34a.

#### PB-4 · Tips in the results

- **Metorite task.** 1950.
- **Scope.** `tips` beside `protocol` (SW-56): at most 2 lines of 120 characters, chosen by a rule table on the tool and the context. Each names a topic and shows once for each run. The edge keeps no state, so the gateway stores which tips a run has seen in a column `pm_agent_runs.tips_seen` (migration, expand only). They are separate from the safety lines.
- **Done when.** A claim of a delegated task with no brief carries the brief tip once. The same run sees it once. An organization that switched tips off sees none, and still sees the safety lines.
- **Fence.** `test_mcp_edge_context.py`, `test_mcp_edge_prompts.py`.
- **Risk.** HIGH (a migration). **Gate.** AGENT-SAFE. **Depends on.** PB-1, SW-56.

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

---

## 8. Verify commands

| Slice | Command |
|---|---|
| PB-1 | `uv run pytest -q -p no:cacheprovider tests/unit/test_playbook_content.py tests/unit/test_playbook_org_neutral.py` |
| PB-2 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_prompts.py` |
| PB-3 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_session.py tests/unit/test_mcp_edge_coverage.py` |
| PB-4 | `uv run pytest -q -p no:cacheprovider tests/unit/test_mcp_edge_context.py tests/unit/test_mcp_edge_prompts.py` |
| PB-5 | `uv run pytest -q -p no:cacheprovider tests/unit/test_playbook_org_neutral.py tests/unit/test_agent_guidance_r8.py` |
| PB-6 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_setup_proposal_r8.py tests/unit/test_mcp_edge_coverage.py` |
| PB-7 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_doctor_r8.py tests/unit/test_needs_attention_r8.py` |
| PB-8 | `uv run pytest -q -p no:cacheprovider tests/unit/test_projects_chat_coverage.py` |
| PB-9 | `uv run pytest -q -p no:cacheprovider tests/unit/test_agent_lanes_r8.py` |
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
