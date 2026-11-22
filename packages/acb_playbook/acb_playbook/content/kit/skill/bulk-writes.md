---
slug: kit/skill/bulk-writes
version: 1
title: Kit skill: bulk writes
summary: How to change many tasks in one call with update_tasks.
audience: both
---

# Bulk writes

Use `update_tasks` to change many tasks in one call. One call takes at most 50 rows.
For more rows, send more calls of 50 or less.

## Rules

- Read first. Call `list_tasks` or `get_plan`, and keep the version of each task.
- Send the version with each row. A stale version fails that row only.
- Read the result for each row. A row can fail while the others pass.
- Retry only the failed rows, after you fix the cause.
- A long call can stop early. The rows it did not try come back marked as not tried. Send them again.
- Name each task once in a call. A task id that appears twice is refused.
- Send a parent change as its own item, with no other field.
- A bulk write cannot delete a task. Deletion needs a person and a separate tool.

## Before a large change

Show the person the list of rows and the change. Make the change after the person agrees.
Write one comment on the parent task with the count of rows that changed.
