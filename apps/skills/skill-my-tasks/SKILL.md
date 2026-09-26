# skill-my-tasks

Task tools for `agent-task-manager`. Every tool operates on the one task
store (`pm_tasks` + `pm_task_personal`, D53) through the gateway. It uses the
internal token and `X-User-Email`. There is no connected PM tool and no
connector (D52).

Since S8a (2026-09-23) the tools call the routes the browser calls. The
contract of record is `workbench/control_plane/src/app/tasks/lib/lens.ts`.
The fence is `tests/unit/test_skill_task_lens.py`.

| Tool | Route |
|---|---|
| `my_tasks_capture(title, notes)` | `POST /projects/my/tasks`, then `POST /tasks/ai/atomize` for a duplicate check |
| `my_tasks_capture_many(lines)` | `POST /tasks/ai/atomize`, then `POST /projects/my/tasks/batch` |
| `my_tasks_list(view, query, context)` | `GET /projects/my/inbox`, paged to the end |
| `my_tasks_list_projects()` | `GET /projects/my/areas` and `GET /projects/nodes` |
| `my_tasks_detail(item_id)` | `GET /projects/my/tasks/{id}`, the timeline, the attachments, and `GET /projects/my/tasks/{id}/lanes` |
| `my_tasks_clarify(item_id)` | `POST /tasks/items/{id}/clarify` |
| `my_tasks_organize(item_id, kind, …)` | `POST /projects/my/tasks/{id}/organize`, one transaction |
| `my_tasks_update(item_id, …)` | `PATCH /projects/tasks/{id}` for shared fields, `PATCH …/personal` for mine |
| `my_tasks_complete(item_id, undo)` | `POST /projects/tasks/{id}/complete`. An undo is `PATCH /projects/tasks/{id}/personal` with NEXT only |
| `my_tasks_move(item_id, to)` | `PATCH /projects/tasks/{id}/personal` |
| `my_tasks_set_stage(item_id, stage)` | `GET /projects/my/tasks/{id}/lanes`, then `PATCH /projects/tasks/{id}` |
| `my_tasks_delegate(item_id, …)` | `PUT …/assignees` and the overlay, or `organize` when a project is named |
| `my_tasks_subtasks` / `my_tasks_add_subtasks` | `GET /projects/tasks?parent_task_id=`, `POST /projects/tasks` |
| `my_tasks_archive(item_id, restore)` | `POST /projects/tasks/{id}/archive` or `/unarchive` |
| `my_tasks_schedule` / `my_tasks_unschedule` | `PATCH /projects/tasks/{id}/personal` |
| `my_tasks_list_schedule(from, to)` | `GET /projects/my/calendar` |
| `my_tasks_accounts()` / `my_tasks_sync()` | No call. They answer that no tool is connected |
| `my_tasks_inbox_insights`, `my_tasks_people`, `my_tasks_plan_project` | `/tasks/insights`, `/tasks/people`, `/tasks/plan*` |
| `my_tasks_plan_day`, `my_tasks_replan_day`, `my_tasks_rollover`, `my_tasks_day_digest`, `my_tasks_estimate_stats`, `my_tasks_set_one_thing` | `/tasks/calendar/*` |

The tools apply these five rules.

1. A title, a note and a due date are facts about the work. Everybody assigned
   sees them. A disposition, a context, a block and a tickler are yours alone.
2. DONE goes through `/complete`, never through the overlay. The board and the
   list agree at the same instant.
3. **Stages group, statuses write (D79).** A status write is always to one
   exact status of the task's own set. The tools read that set from
   `GET /projects/my/tasks/{id}/lanes`. That route checks membership, so it
   also works for a board task that the member gets by assignment only.
   * The tool writes a value that is a status name, in any case.
   * The tool writes a stage word ("to do", "in progress", "done") only when
     the task's set has ONE status in that stage.
   * When the stage has two or more statuses, the tool writes nothing. It
     lists the statuses. **Do not guess a status. Ask the member which one.**
   * An unknown name writes nothing and lists every status as
     "Name (Stage)". `my_tasks_detail` lists them the same way and marks the
     current one.
4. An undo of Done writes NEXT and nothing else. The gateway reopens the
   task into its first To do status (`reopen_if_closed`). The skill has no
   reopen rule of its own.
5. Every status write result names the status and the project, as the
   app's undo toast does: "Moved to In review · Website relaunch". A task in
   the member's own tree names no project.

Env: `GATEWAY_URL` (default `http://localhost:8080`), internal token from
settings or `LITELLM_MASTER_KEY`. The acting user comes from the per-run
ContextVar the executor binds from the run payload's `user_email`, and from
nowhere else. The old `ACB_AGENT_USER_EMAIL` env fallback was a process-global
that no run cleared, so it handed an unattributed run the last user.
