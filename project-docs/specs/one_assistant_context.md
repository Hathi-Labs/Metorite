# One assistant: context that survives, and tool packs

**Status:** ✅ ACTIVE (2026-10-25). Board: **WS-67**. Decision: **D116**.
OA-1 and OA-2 are BUILT (PR #1341, 2026-10-25, "As built" below). OA-5 is
BUILT (2026-10-25, branch `ws67-oa5-measure-mt-4cd6b9f1`): the report is
`docs/one_assistant_measure_2026-10-25.md`. OA-3 and OA-4 are dispatchable.

OA-6 is an owner gate, and its input is now ready. OA-7 to OA-10 wait for
OA-6. Metorite: epic #445 in *AI Chat Interface*, with slices #446 (OA-1) to
#455 (OA-10).

**As built, OA-5.** `evals/one_assistant_measure` runs 13 email chats (18
turns) through the one harness of the routing eval, two ways. One way
delegates to the email assistant, as today. The other way is an eval-only
tool pack. Fence: `tests/unit/test_one_assistant_measure.py`.

The live run of 2026-10-25 ran 3 times on each arm. The arms passed 51 of
54 turns (delegation) and 52 of 54 (pack). The median turn took 22.6 s and
10.0 s, and the pack cost half as much. The Navy shape passed 6 of 6 on
both arms, so no answer named CB6 or CB7 as a part.

The measure did not read `agent.run_timing` or `usage_event`, because only
the gateway and the Console write them. The harness timed each turn and
priced each call instead.

**As built, OA-1 and OA-2.** `acb_skills/handoff.py` owns what crosses a
handoff. The top-level run binds `member_turns`: the member's typed words of
the chat, newest last, at most 8 and 6000 characters. The current turn comes
from `assistant_profile.MEMBER_TYPED`, never from `payload["message"]`, so a
Continue note cannot pose as the member. A paste is not the member's words.
`head_task()` heads every delegated task with the words, the rule that they
outrank the task, and the reply rules of OA-2. The streaming seam and the
four batch sites call it. The tier question reads the task only
(`task_of`). Fences: `tests/unit/test_handoff_words.py` and
`evals/trajectories/test_handoff_words_trajectory.py`. Open: a steer that
the gateway replays after a run died is in neither the history nor the
typed words, so it does not reach the head. OA-5 measured the live half of
"no CB6" (above). It did not check the reply marks of OA-2.

**Owner:** WS-67. **Amends:** nothing yet. OA-6 decides if D99's
"specialists behind it" changes to "tool packs in it" for conversational
work. **Composes with:** `navigation_shell.md` §7.1 (D99, one assistant),
`skills_registry.md` (WS-23, the skill-family registry),
`email_app_master_plan.md` §21 (the held-chat guard),
`multi_agent_orchestration.md` (WS-12, delegation), and the chat-speed
work of #1313 and #1323.

---

## 1. Why this spec exists

On 2026-10-25 the owner asked the assistant about a Navy order in the
Fracktal mailbox. The chat is session `de64d903` in the `fracktalworks`
organization. The assistant named the two parts "CB6 and CB7". That name
came only from the file name of a sample attachment. The owner then said
that the attachments are only references. The assistant still used the
name in its next answer.

The trace shows four causes. Each one is in the design, not in one prompt.

| # | Cause | Evidence |
|---|---|---|
| C1 | The attachment reader has no OCR. A scanned PDF gives no text, so a file name is the only signal about its content. | `routes/email/transport/attachments.py`, `_NO_TEXT_REASON`. All three PDFs answered "no readable text". |
| C2 | A specialist answers in prose. Nothing marks a quote and a guess apart, so a hedge becomes firmer at each hop. | Turn 2: "strongly suggests" (email assistant), then "almost certainly" (orchestrator), then "designated" (turn 3). |
| C3 | The member's words do not reach the specialist. It has no memory of the chat. The rules carry links and draft text, not corrections. | Turn 3: the task held "(e.g. CB6/CB7)" and not the member's note. `orchestrator/agents.py`, the SPECIALIST AGENT TOOLS block. |
| C4 | The chat stores no correction. On the next turn the assistant reads its own earlier claim in the history and trusts it. | Turn 3: the email assistant said that CB6 appears only in a file name. The orchestrator still wrote "designated CB6 and CB7". |

Both runs used `deepseek/deepseek-v4-pro` (the Router's `usage_event`). The
model reads no image. The model is a factor, and it is not the cause.

**A PDF with a text layer already reads.** C1 is about scanned pages and
images only.

## 2. The decision this spec carries (D116)

Owner decision, 2026-10-25, in chat: take the recommendations.

1. Fix the handoff now (OA-1, OA-2). It helps for as long as a handoff
   exists.
2. Add OCR (OA-3). The owner chose a library, not an image model.
3. Store corrections in the chat (OA-4).
4. Measure before any rebuild (OA-5). Then the owner decides (OA-6).
5. The target shape, if OA-5 supports it: one assistant holds the
   conversation and loads each app's tools as a **tool pack**. A
   sub-agent stays only for a heavy job, with a structured handoff.

## 3. The target shape

| Part | Now | Target |
|---|---|---|
| Who holds the conversation | The orchestrator, and each specialist sees only a task text | One assistant, with the member's words, corrections and working sets |
| How an app joins a turn | A specialist agent with its own prompt and tools | A tool pack: the app's tools plus a short rules block, loaded when the turn needs it |
| Heavy jobs (many mails, long reads) | The same specialist | A sub-agent with the contract of §6 |
| Guards (held chat, cards) | In the email tools | Unchanged. The guard is in the tools, not in a prompt |

A tool pack uses the WS-23 registry (`acb_skills/skill_families.py`). Today
the email tools are an agent's own tools and are not in that registry. OA-7
moves them.

## 4. Slices

| Slice | What | Risk tier | Gate |
|---|---|---|---|
| **OA-1** | The orchestrator copies the member's words verbatim into each task: the request, and every correction or limit in this chat. It never puts its own guess in a task as an example. | MEDIUM | none |
| **OA-2** | A specialist reply marks each fact as **quoted** (with its source) or **inferred**. The orchestrator never states an inferred item as fact, and never makes a hedge firmer. A file name is a label, never content. A "sample" or "for reference" attachment is never the subject of the order. | MEDIUM | none |
| **OA-3** | OCR for a scanned PDF and an image in `read_attachment`. It runs off the event loop, with a page cap and a time cap. The result says that the text came from OCR. | HIGH (new dependency, untrusted file input) | none |
| **OA-4** | Chat constraints: a correction or a limit that the member states goes into the chat's working set, beside the email working set. The per-turn context shows it. The member can see and remove it. | MEDIUM | none |
| **OA-5** | Measure. Run the email scenarios two ways: email tools loaded into the one assistant, and delegated as now. Compare accuracy, latency and cost. | LOW (eval only) | none |
| **OA-6** | The owner decides from the OA-5 report. A yes amends D99 for conversational work. | — | **OWNER** |
| **OA-7** | The email tool pack: the email tools join the WS-23 registry, and the email rules become the pack's rules block. | HIGH (the egress guard moves with the tools) | OA-6 |
| **OA-8** | The Projects and CRM tool packs. | MEDIUM | OA-7 |
| **OA-9** | The sub-agent contract of §6 for heavy jobs. | MEDIUM | OA-6 |
| **OA-10** | Retire conversational delegation. A specialist agent stays for a scheduled job, a workflow and an external agent. | HIGH | OA-8, OA-9 |

## 5. Acceptance

- **OA-1.** A recorded replay of session `de64d903` turn 3 sends a task that
  holds the member's note word for word, and holds no "CB6".
  Fence: a trajectory test in `evals/trajectories/`.
- **OA-2.** In the same replay, the answer does not name CB6 or CB7 as the
  parts. A unit test checks the reply format of the email assistant: an
  inferred item carries its mark.
- **OA-3.** A scanned PDF fixture gives its text. A 200-page scan stops at
  the cap with a plain sentence. A malformed file gives the existing
  refusal, never a crash. The OCR text is marked as OCR.
- **OA-4.** A correction in turn N shows in the per-turn context of turn
  N+1 and N+5. Its removal by the member takes it out.
- **OA-5.** A report in `docs/` with the numbers for both shapes: the pass
  rate of the email trajectory suite, the Navy replay, the live-model sweep,
  `agent.run_timing` (first text and total) and `usage_event` cost.
- **OA-7.** Every held-chat and card test passes with the tools in the
  pack. The egress pin test (`test_egress_card_bound_pin.py`) passes
  unchanged.

## 6. The sub-agent contract (OA-9)

**Input.** The member's words verbatim, the chat constraints of OA-4, the
ids of the working set, and the job. No summary of the chat.

**Output.** A structured result, not prose. Each item has a value, a kind
(`quoted` or `inferred`) and a source (an email id, a file name and a
page, or a tool). The orchestrator renders the answer from these items.

## 7. Coordination with the chat-speed work

The chat-speed work (#1313, #1323) owns latency. These points touch it:

1. **The delegated path.** #1323 lists "a delegated agent still loads twice
   and streams no text" and "skip the orchestrator rewrite of a delegated
   answer" as next slices. OA-7 can remove delegation for email
   conversation. Build those slices for the paths that stay after OA-10:
   the sub-agent of OA-9, scheduled jobs and workflows.
2. **Fewer model rounds per email run.** #1323 says this needs evals first.
   OA-5's harness is those evals. Share it, and do not build a second one.
3. **The timing lines.** OA-5 reads `agent.run_timing` and
   `executor.run_setup_ms`. Keep their names and fields stable.
4. **OCR.** OA-3 adds time to `read_attachment`. It runs off the event
   loop with a cap, so it must not block other runs.
5. **OA-1 adds tokens.** A task now holds the member's words. Expect a
   small rise in the input tokens of a delegated run.

HANDOFF **H-412** carries these points to that session.

## 8. Out of scope

- An image-reading model for drawings. The owner chose OCR first.
- WhatsApp (D115).
- A change to the held-chat guard itself.
