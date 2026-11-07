---
slug: failures
version: 1
title: Failure causes and their guards
summary: A fixed list of causes for a stalled run, each with a guard.
audience: both
---

# Failure causes and their guards

Use one cause from this list for each stalled run. A fixed list lets you count and compare.

| Cause | What happened | A guard |
|---|---|---|
| `ci_wait` | The run waited for automatic checks and the session ended. | Let the run park on the checks and wake when they end. |
| `no_disposition` | The turn ended and the run had no result, question or release. | Block the end of a turn that has an open run. |
| `question_terminal_only` | The agent asked a question only in a terminal that nobody reads. | Ask on the task so that a person sees it. |
| `answer_unread` | A person answered and no session read the answer. | Wake or start a session when an answer arrives. |
| `merge_not_linked` | The change merged and the task stayed open. | Link the change to the task, and close the task from the merge. |
| `session_dead` | The session ended and left a claim. | Give each claim a lease, and release a claim that goes quiet. |
| `epic_hold` | An agent held a parent task for days while the sub-tasks carried the work. | Claim the slice, not the epic. |
| `box_down` | The machine stopped. | Report machine health, and restart the sessions in one step. |
| `signed_out` | The agent lost its sign-in. | Use a sign-in that does not need a person each time. |
| `capacity` | There was no room to start the run. | Show the reason when a task cannot start. |
| `human_wait` | The run waited for a person. | Name the person and the action, and show who waits. |
| `other` | None of the above. | Add a new cause only after you see it three times. |

## Check yourself

Which cause is first on your list of stalled runs this week?
