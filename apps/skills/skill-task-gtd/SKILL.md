# skill-task-gtd

Task tools for `agent-task-manager`. Every tool operates on the one task
store (`pm_tasks` + `pm_task_personal`, D53) through the gateway. It uses the
internal token and `X-User-Email`. There is no connected PM tool and no
connector (D52).

Since S8a (2026-09-23) the tools call the routes the browser calls. The
contract of record is `workbench/control_plane/src/app/tasks/lib/lens.ts`.
The fence is `tests/unit/test_skill_task_lens.py`.

| Tool | Route |
|---|---|
| `gtd_capture(title, notes)` | `POST /projects/my/tasks`, then `POST /tasks/ai/atomize` for a duplicate check |
| `gtd_capture_many(lines)` | `POST /tasks/ai/atomize`, then `POST /projects/my/tasks/batch` |
| `gtd_list(view, query, context)` | `GET /projects/my/inbox`, paged to the end |
| `gtd_list_projects()` | `GET /projects/my/areas` and `GET /projects/nodes` |
| `gtd_detail(item_id)` | `GET /projects/my/tasks/{id}`, the timeline, the attachments, the lanes |
| `gtd_clarify(item_id)` | `POST /tasks/items/{id}/clarify` |
| `gtd_organize(item_id, kind, …)` | `POST /projects/my/tasks/{id}/organize`, one transaction |
| `gtd_update(item_id, …)` | `PATCH /projects/tasks/{id}` for shared fields, `PATCH …/personal` for mine |
| `gtd_complete(item_id, undo)` | `POST /projects/tasks/{id}/complete` |
| `gtd_move(item_id, to)` | `PATCH /projects/tasks/{id}/personal` |
| `gtd_set_stage(item_id, stage)` | `GET /projects/nodes/{project}/statuses`, then `PATCH /projects/tasks/{id}` |
| `gtd_delegate(item_id, …)` | `PUT …/assignees` and the overlay, or `organize` when a project is named |
| `gtd_subtasks` / `gtd_add_subtasks` | `GET /projects/tasks?parent_task_id=`, `POST /projects/tasks` |
| `gtd_archive(item_id, restore)` | `POST /projects/tasks/{id}/archive` or `/unarchive` |
| `gtd_schedule` / `gtd_unschedule` | `PATCH /projects/tasks/{id}/personal` |
| `gtd_list_schedule(from, to)` | `GET /projects/my/calendar` |
| `gtd_accounts()` / `gtd_sync()` | No call. They answer that no tool is connected |
| `gtd_inbox_insights`, `gtd_people`, `gtd_plan_project` | `/tasks/insights`, `/tasks/people`, `/tasks/plan*` |
| `gtd_plan_day`, `gtd_replan_day`, `gtd_rollover`, `gtd_day_digest`, `gtd_estimate_stats`, `gtd_set_one_thing` | `/tasks/calendar/*` |

Three rules the tools apply.

1. A title, a note and a due date are facts about the work. Everybody assigned
   sees them. A disposition, a context, a block and a tickler are yours alone.
2. DONE goes through `/complete`, never through the overlay. The board and the
   list agree at the same instant.
3. A stage is a lane NAME in the task's own project. The tool resolves it to
   a `status_id`. An unknown name returns the valid names.

Env: `GATEWAY_URL` (default `http://localhost:8080`), internal token from
settings or `LITELLM_MASTER_KEY`. The acting user comes from the per-run
ContextVar the executor binds from the run payload's `user_email`, and from
nowhere else. The old `ACB_AGENT_USER_EMAIL` env fallback was a process-global
that no run cleared, so it handed an unattributed run the last user.
