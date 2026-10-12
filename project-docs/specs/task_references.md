# Task references — links, pull requests, commits, pasted images and `#123` pills

**Status:** 🆕 PROPOSED 2026-10-13, owner directive. Nothing is built. Verified against code on 2026-10-13 at `origin/main` 68e26cad.

**Board row:** WS-57 (`work_plan.md` §2).

**Owner of the table:** this spec builds `pm_task_refs`, which `external_agent_access.md` §11 proposed. That spec keeps the MCP tool (EA-6). This spec keeps the table, the route, the task card and the in-product chat tools.

## 1. What the owner asked for

The owner asked for five things on 2026-10-13:

1. A task card takes docs, links and images.
2. A task links to GitHub pull requests, issues, commits and comments. Plane and Paca are the references.
3. The in-product agent links them. Later an outside agent links them through MCP.
4. Ctrl+V of an image in the description adds it as an attachment.
5. `#123` in the title, the description or a comment draws as a pill, and adds a linked task. A project link draws as a pill too, but links nothing.

## 2. How other tools do it (research, 2026-10-13)

- **Plane.** A work item has a Links list (a URL and a title) and Attachments. Its GitHub integration links a PR whose title holds `[WEB-344]`, and moves the work item's state with the PR. A key with no brackets links and does not move the state.
- **Linear.** A branch name or "Fixes ENG-123" in a PR links the issue. The issue shows the PR and its state. An automation moves the issue when the PR merges.
- **OpenProject.** A work package has a GitHub tab. It lists each PR with its state (open, merged) and its check runs. `OP#87` in a PR description links it. A PR and a work package are many-to-many.

**What we take.** A list of typed references on the card, with the PR state shown. Many-to-many. A reference added by a person, by the chat agent or by an outside agent. **What we leave for later.** Webhook linking by a key in a PR title (§8), and moving the status when a PR merges.

## 3. Scope and non-goals

**In scope:** TR-1 to TR-5 (§6).

**Not in scope:**
- A GitHub App or a webhook. `external_agent_access.md` §14 names it as later work, and an App is a third-party registration (OWNER-GATE).
- A status change when a PR merges. It needs the webhook.
- A preview of any URL that is not GitHub. A server fetch of an arbitrary URL is a request-forgery risk, so a generic link shows its host and the title the member typed.
- A rich-text editor. WS-27ao (`project_management_app.md` §9.4.2) owns it. TR-4 keeps the plain field for editing and renders the saved text.
- The MCP tool `add_task_reference`. EA-6 owns it, and it calls the TR-1 route.

## 4. The seams this uses

| Seam | Where | Use |
|---|---|---|
| Task-to-task links | `pm_task_links`, `routes/projects/tasks.py` POST `/tasks/{id}/links` | TR-5 adds `relates_to` through it |
| Attachments | `routes/projects/attachments.py`, `attachmentsApi` in `projects/lib/api.ts` | TR-4 uploads a pasted image through it |
| The org's GitHub token | `gateway/connections/github.py` `token_or_none` | TR-1 reads a PR's title and state |
| The pill | `components/ui/EntityPill.tsx` | TR-2 and TR-5 draw with it |
| `#123` parsing | `src/lib/taskRefs.ts` (`findTaskRefs`, `resolveTaskRefs`, `TaskRefPill`), built by the chat session on branch `task-ref-autolink` | TR-5 imports it. No second parser |
| Markdown | `MarkdownBody` in `components/MarkdownMessage.tsx` | TR-4 renders the description |
| Activity | `pm_task_activity` type `link` | Each add or remove writes one row |

## 5. The table

`pm_task_refs`, as `external_agent_access.md` §11 proposed, with two more kinds and three more columns.

| Column | Type | Note |
|---|---|---|
| `id` | uuid | |
| `organization_id` | uuid | Tenant scope. Under the generated RLS policy (R5) |
| `task_id` | uuid | Foreign key to `pm_tasks`, on delete cascade |
| `kind` | text | `pull_request`, `issue`, `commit`, `branch`, `comment`, `doc`, `url` |
| `provider` | text | `github`, `gitlab`, `bitbucket`, `google`, `figma`, `notion`, `other` |
| `ref` | text | `owner/repo#12`, `owner/repo@sha`, `owner/repo:branch`, or the canonical URL |
| `url` | text | The URL as given, canonicalised |
| `title`, `state` | text | `state` is `open`, `draft`, `merged` or `closed`, or null |
| `fetched_at` | timestamptz | When TR-1 last read the title and state from the provider |
| `via` | text | `ui`, `chat`, `mcp:<client>` or `text`. `text` means TR-5 found the URL in the task's text |
| `created_by`, `created_at`, `updated_at` | | |

- A unique key on `(organization_id, task_id, kind, provider, ref)` makes an add idempotent.
- The migration is expand-only (R6). Take the number at build time and check it again at merge (R1).

## 6. The slices

### TR-1 — the table, the classifier and the route (AGENT-SAFE)

- **The classifier** is a pure function in the gateway. It takes a URL and returns `kind`, `provider`, `ref` and a short label. It knows these GitHub shapes: `/pull/N`, `/issues/N`, `/commit/SHA`, `/tree/BRANCH`, and an issue or PR comment `#issuecomment-N`. It also knows GitLab merge requests and Google Docs, Sheets and Slides. Every other `http(s)` URL is `url`. Any other scheme is refused with 422.
- **The route.** `GET /projects/tasks/{id}/refs`, `POST /projects/tasks/{id}/refs` with `{url, title?}`, and `DELETE /projects/tasks/{id}/refs/{ref_id}`. A write needs edit access to the task. A read needs read access, as the links route does today.
- **The GitHub read.** The route reads the title and state through the org's GitHub token. It does this on an add, and on a read when `fetched_at` is older than 15 minutes. It reads at most 10 references per call, with a 3-second timeout each. With no token, or on any failure, the reference keeps what it has and the route still answers. The token never reaches the client.
- **Done when:** the classifier passes a table test of at least 20 URLs. The route adds, lists and removes a reference, and a second add of the same URL returns the first row. A read for another org's task returns 404 against a real database (R8). Each add and remove writes one `link` activity row.

### TR-2 — the Links section on the card (AGENT-SAFE)

- A **Links** section in `TaskBody`, beside Files. A field takes a pasted URL, and Enter adds it.
- Each reference is one row. It shows the provider icon and the label (`metorite#927`, or the title when known). A PR or an issue also shows a state chip. Merged is the done colour and closed is the muted one. The row opens the URL in a new tab. A remove button sits at the end of the row.
- The section shows in both hosts, Projects' `TaskPanel` and My Tasks' `ItemDetail`, because `TaskBody` is shared.
- **Done when:** a pasted GitHub PR URL shows as `repo#N` with its state. A Google Doc shows its icon. A remove takes the row away. The visual rig shows the section in dark, light and compact, and at 390 px.

### TR-3 — the chat tools (AGENT-SAFE)

- `add_task_link(task, url, title?)` and `remove_task_link(task, url)` in `skill_projects/writes.py`. Both call the TR-1 route, so the chat agent and a person write one path.
- `task_detail` in `reads.py` lists the references.
- **Done when:** a scripted chat run adds a PR link to a task, and the card shows it. The tool registry fences in `skill_projects` pass.

### TR-4 — the description renders, and a pasted image attaches (AGENT-SAFE)

- **View.** The saved description renders through `MarkdownBody`: paragraphs, lists, links, code and images. A click on it, or the edit button, opens the plain field as today.
- **Paste and drop.** An image pasted or dropped into the description field or the comment composer uploads through the attachment route. While it uploads, the field holds `![Uploading name…]()`. When the upload ends, that becomes `![name](<attachment url>)`. A failed upload removes the placeholder and says so in a toast.
- A file that is not an image, dropped on the card, uploads as an attachment and inserts nothing.
- **Done when:** a pasted PNG shows in the Files list and inline in the rendered description. A dropped PDF shows in Files only. A file over the size limit shows the route's refusal.

### TR-5 — `#123` pills and links from the text (AGENT-SAFE, after `taskRefs.ts` lands)

- **Pills.** The title, the rendered description and each comment draw `#123` through `TaskRefPill`. A link to a project (`/projects?project=<id>`) draws as a project pill.
- **Links from text.** When a person or an agent saves a title, a description or a comment, the gateway finds each `#N`. For each one that names a visible task in the same organization, other than this task, it adds a `relates_to` link. A GitHub or doc URL in the same text adds a TR-1 reference with `via = text`.
- **Never removed by an edit.** Deleting `#123` from the text keeps the link. A person removes a link from the Links section.
- **One grammar, two languages.** The gateway's `#N` finder is Python, and the client's is `findTaskRefs`. One JSON file of cases, `tests/fixtures/task_ref_cases.json`, drives both tests, so the two cannot drift.
- **Projects.** A project pill links nothing. A task belongs to one project already, so a task-to-project link adds little. A project pill opens the project.
- **Done when:** `#12` in a description draws as a pill with the task's title, and the task shows "relates to #12". `#9999` that names no task stays text. The shared case file passes in vitest and in pytest.

## 7. Verification commands

```
uv run pytest tests/unit/test_task_refs_classifier.py tests/unit/test_task_refs_route.py -q
uv run pytest tests/unit/test_task_refs_text_links.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/projects src/lib/taskRefs.test.ts
cd workbench/control_plane && npx playwright test e2e/visual/<rig>.visual.ts --project=visual
```

R8: run the route test against a private database (`bash scripts/dev_db.sh`), never the shared scratch one.

## 8. Later work, named so it is not built by accident

- **A GitHub App with a webhook.** A PR whose title or branch holds the task key links itself, and a merge can move the status. `external_agent_dispatch.md` D-DX-2 proposes the App. Registering it is an OWNER-GATE.
- **The task key.** A short key per project (`MET-123`) to put in a branch name. Today a task has only `#N`.
- **Unfurl for other providers,** through an allow-list of hosts, after a request-forgery review.

## 9. Change log

- 2026-10-13 — Written. Research of Plane, Linear and OpenProject in §2. The table follows `external_agent_access.md` §11, with `issue`, `comment` and `doc` added, and `fetched_at` and `via` as columns.
