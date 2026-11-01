# Chat parity — each UI action is also a chat action

**Status: ACTIVE. P-0 is BUILT (2026-11-01, branch
`chat-parity-p0-mt-7f1c2417`).** The fence `tests/unit/test_chat_parity.py`
holds each request of the control plane to one decision. Today's gaps sit in
a debt list, so CI passes. P-1 (Calendar and Tasks) is the next slice.

**The fence blocks every pull request** (owner decision, 2026-11-01). It is a
SAFETY fence in `tests/fences.json`, so it blocks in build mode too. The
ceilings in `chat_parity.py` are the baseline at `origin/main` `bdb75484f`.
From that point they only go down.

**Epic:** Metorite task 1238. Written 2026-11-01.

**Owner rule, 2026-11-01, in chat.** The words are the owner's, so they sit
in a block that the linter does not read:

```text
"every single possible action that is possible to do via button clicking
and the UI/UX should be possible to do via the chat interface across the
entire application."
```

## 1. The rule

Each action that a member can do in the app is also possible from the chat.
A read is an action too: the chat can read each list that the app shows.

**An action** is one request that the control plane sends to its BFF. It is
a verb and a path under `/api/`, with `{}` for each id. Each action has
exactly one decision in `packages/acb_skills/acb_skills/chat_parity.py`:

| Table | What it holds |
|---|---|
| `COVERED` | The chat tool that sends the same request to the gateway |
| `EXEMPT` | A kind of `EXEMPT_KINDS` and a reason |
| `DEBT` | The wave of the epic that gives the chat a tool |

Two apps keep their own tables, and the parity module does not copy them.

- **Projects.** Fence F3 holds each method of the Projects client
  (`projects_agent_parity.md` §6.4). A request to a Projects route from any
  other file takes its decision from the row of the Projects manifest.
- **Knowledge.** A request to a Knowledge route takes its decision from the
  Knowledge manifest (`acb_skills.knowledge_manifest`).

The parity module takes a row for a route of these two apps only where the
manifest gives no built tool.

## 2. Exemptions are few, and of a fixed kind

An exemption is only for a request that is not an act of a member in any
sense. `EXEMPT_KINDS` holds the kinds:

| Kind | The request |
|---|---|
| `oauth` | An OAuth redirect, consent or callback. The provider needs the browser |
| `download` | A file stream for a download, a preview or a picture |
| `stream` | A live stream, a poll or a heartbeat that refreshes a view |
| `webhook` | A call that a provider or a machine makes |
| `health` | A health ping, or a probe of the session |
| `session` | Sign-in, sign-up, sign-out or a switch of account |
| `chat` | The chat's own transport: a turn, a stop, an answer to a card |
| `secret` | The member types a password, a key or a token |

A new kind needs the owner. Do not add a kind to make a row pass.

**Secrets stay in the UI** (owner decision, 2026-11-01). A member types a
password, a key or a token only in the form of the app. No chat tool takes
one. So the `secret` kind stays, and its rows are not debt.

## 3. The fence and the ratchet (R7)

**Fence:** `tests/unit/test_chat_parity.py`. Its reader is
`tests/unit/_chat_parity_reader.py`. It runs no node and no gateway. It sits
in the `safety` list of `tests/fences.json`. The pr-check shards run each
safety fence and block on it, with no `harden` label.

1. The reader reads each request of the UI from the TypeScript source, with
   the tokenizer of F3. It follows a literal path, a constant, an object of
   constants, a path builder, and a request seam. A seam is a function that
   takes a path and calls `fetch`. A seam can come from another file.
2. Each action is in exactly one table, or its manifest decides it. A new
   action fails. The message names it, the file and the function that send
   it, and its gateway route.
3. A row that names an action that the UI no longer sends fails.
4. The reader also reads each tool function in `TOOL_SOURCES`, and the
   helpers that it calls. A `COVERED` tool must be a registered tool. Its
   source must name the gateway route of the action, with the same verb.
5. The debt of each app must equal `DEBT_CEILING`. A new debt fails. A paid
   debt lowers the ceiling in the same change. So the list only shrinks.
6. A request that the reader cannot follow is UNREAD. The count of each file
   must equal `UNREAD_CEILING`. Write a new path as a literal, or send it
   through a seam.

Mutation tests in the same file prove the fence. A new action fails. So do
a stale row, a false claim, a bad exemption and a new debt.

## 4. What the fence cannot judge

- **The body of a request.** The fence holds the verb and the path. Two
  buttons that send different fields to one route are one action. Fence F2
  holds the fields of Projects only.
- **A tool that names a route as an inner step.** A write tool can read a
  list first. The fence then counts the tool as one that can read the list.
  A reviewer checks that a `COVERED` row is the same act.
- **A tool that works in the process.** The workflow tools and the memory
  tools call the gateway's code, not its routes. The fence cannot see that,
  so their actions stay in `DEBT` until a tool sends the route.

## 5. The waves

`WAVES` in the parity module holds the waves. P-1 is Calendar and Tasks. The
epic sets the order of the waves after it. Each wave moves its rows from
`DEBT` to `COVERED`, and lowers the ceiling of its app.

**A new UI action needs a chat tool, or a row in the parity table.**
`workbench/control_plane/AGENTS.md` carries this rule to UI work.
