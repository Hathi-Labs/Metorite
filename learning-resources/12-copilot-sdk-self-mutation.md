# 12 · GitHub Copilot SDK & Self-Mutation

This is the platform's most unusual capability: **agents that fix their own bugs.** When an agent's code
throws, the system spawns a sandboxed coding agent that reads the error, edits the failing agent's *own*
repository, runs its tests, and opens a pull request — all automatically, capped and human-gated. The
engine behind this is the **GitHub Copilot SDK**. This chapter explains the SDK's two roles and the
self-mutation loop.

---

## 1. What the GitHub Copilot SDK is

`github-copilot-sdk` (Python) is a library that drives the **GitHub Copilot CLI** as an autonomous agent.
Unlike a plain model API, it comes with a built-in agent runtime that has **real tools out of the box**:
shell command execution, file read/write, running scripts, and MCP servers. You give it a task in natural
language and (in `autopilot` mode) it plans and executes using those tools, streaming its reasoning and
tool activity.

Two properties make it special for this platform:

- **It's a coding agent with hands.** Shell + file I/O means it can clone, edit, test, and commit code —
  exactly what "fix a bug" requires.
- **BYOK.** A `provider` config routes its LLM calls through any OpenAI-compatible endpoint (the
  platform's `/v1` gateway, chapter 09) and your keys, instead of Copilot's cloud backend.

---

## 2. Its two — and only two — roles

The platform is strict about where the Copilot SDK may appear (a global constraint):

1. **As a model backend wrapped inside MAF.** `GitHubCopilotAgent` (and Metorite's subclass
   `MetoriteCopilotAgent`) makes the Copilot SDK look like an ordinary MAF agent. It participates in
   handoffs, gets platform tools injected, and streams through AG-UI like any other agent. Here the
   Copilot SDK is *just another way to run an agent* — MAF is still the runtime (chapter 11).

2. **Standalone, inside the self-mutation sandbox.** This is the only place application code touches the
   SDK directly, and it's isolated in an ephemeral Docker container.

It is **never** called directly by ordinary application code, and it is **not** related to CopilotKit (the
React UI) or LangChain. Keeping these boundaries crisp is what prevents the "which Copilot?" confusion from
chapter 01.

### The `MetoriteCopilotAgent` subclass

To use the SDK as a MAF agent, Metorite subclasses `GitHubCopilotAgent` and adds two things at
runtime (monkey-patched onto loaded agents, so agent repos need zero changes):

- **BYOK forwarding** — patches session creation to pass the `provider` block through to the SDK, routing
  LLM calls through the platform gateway.
- **Rich event streaming** — translates *all* Copilot SDK event types into MAF content so the frontend
  sees the full picture:

| Copilot SDK event | Becomes (MAF → AG-UI) | UI shows |
|---|---|---|
| `ASSISTANT_MESSAGE_DELTA` | text → `TEXT_MESSAGE_CONTENT` | streaming answer |
| `ASSISTANT_REASONING_DELTA` | reasoning → `THINKING_…` | live thinking |
| `TOOL_EXECUTION_START` | function_call → `TOOL_CALL_START` | tool block |
| `TOOL_EXECUTION_COMPLETE` | function_result → `TOOL_CALL_RESULT` | tool result |
| `TOOL_EXECUTION_PARTIAL_RESULT` | text | live terminal output |

---

## 3. Self-mutation — the loop

The distinctive feature. When an agent run raises an exception the platform can't shrug off, it tries to
repair the code. The design (ADR-021, "hot-patch with an audit gate") optimizes for **fast recovery with
human oversight**, not one at the expense of the other.

```
agent run raises an exception
   │
   ├─▶ _self_anneal: up to 2 in-process retries        (transient errors — no code change)
   │
   ▼ still failing
attempt_self_mutation():
   1. Gather failure telemetry: error + stack, the agent's instructions, its inputs, trigger context.
   2. Spawn an ephemeral Docker sandbox (image: acb-mutation-runner) with:
        • the failure telemetry (as env/JSON),
        • BYOK creds pointing at the platform gateway,
        • the agent's PERSISTENT CLONE mounted at /workspace/repo.
   3. Inside the sandbox, a Copilot-SDK coding agent (autopilot):
        • reads the telemetry, locates the bug, writes a fix,
        • runs the agent repo's pytest suite.
   4. Tests PASS → commit the fix to the local clone's main branch  ← fix is LIVE on the next run
                 → push a branch auto-fix/<run_id> and OPEN A PR    ← audit record + rollback trigger
                 → register the commit in `pending_commit` for Control Plane approval
      Tests FAIL → git reset --hard; hold as eval_failed for a human decision.
   5. max_mutation_attempts = 1  →  destroy the sandbox, done.
```

### Why "hot-patch"?

The fix is applied to the **persistent local clone immediately**, so production recovers in *minutes*, not
after a human wakes up and reviews a PR. The PR is **not a gate before recovery** — it's an **audit record
and a rollback switch**:

- **Merge the PR** → the fix is canonicalized in the remote repo; the next `git pull` keeps it.
- **Close the PR** without merging → a GitHub webhook (`pull_request.closed`) hits the platform, which runs
  `git reset --hard origin/main` on the clone, reverting the agent to its pre-fix behaviour.

Closing the PR is the human's "I disagree with this fix" button.

### Why it's safe, not terrifying

Three constraints keep a self-editing system controllable:

1. **`max_mutation_attempts = 1` per failure event.** A bad fix cannot trigger an endless self-editing
   loop — the single most important guardrail.
2. **A human must merge to make it canonical.** The hot-patch is temporary and reversible; permanence
   requires a person.
3. **The fix must pass the agent's own tests** before it's committed at all. Untested changes are discarded.
4. **Isolation.** The sandbox is an ephemeral, network-isolated Docker container that self-destructs after
   the run (spawned via the host Docker socket). It touches only the mounted agent repo.

---

## 4. Why the persistent clone makes this possible

Recall the Dynamic Agent Loader (chapter 06 §3) keeps each agent as a **real Git working tree** in a
persistent cache. That single decision is what makes self-mutation tractable: the thing the sandbox needs
to edit already exists as a proper, authenticated Git repo on disk. There's no separate "checkout for
fixing" step — the live clone *is* the mutation workspace, and a commit to it is instantly live. The same
mechanism gives even repo-less "local" agents rollback safety via a local `git init` baseline (approve =
keep commit, reject = `git reset HEAD~1`).

---

## 5. Generalizing the idea

You don't need the Copilot SDK specifically to build this — any sandboxed coding agent with shell+file
tools (Claude Code, Aider, an OpenHands runner) can play the role. The transferable architecture is:

1. **Capture rich failure telemetry** (error, stack, inputs, the agent's own description of its job).
2. **Give a coding agent an isolated, disposable sandbox** with the failing code mounted and test tooling
   available.
3. **Gate on the code's own tests** before accepting a change.
4. **Apply fast, but make canonicalization human-gated and trivially reversible** (a PR you can close).
5. **Cap attempts** so the system can't runaway-edit itself.

That combination — autonomous repair for speed, human merge for correctness, hard caps for safety — is the
whole idea. It turns "an agent crashed at 3am" from an outage into a PR waiting in the morning.

Next: **[13 · AG-UI & Generative UI](./13-ag-ui-and-generative-ui.md)**.
