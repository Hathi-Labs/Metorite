# Project import — bring work in from another tool by file

**Status: ACTIVE — the ClickUp import is LIVE on production since 2026-09-28. I-8 (the Spaces step and unknown columns) is built, and I-5 is deferred. I-10 (one status set per space, a Map section that opens on a summary) is built, 2026-10-09 (§6.3, §7.7). I-11 (tags and types into the shared vocabulary) is spec only (§6.5), and so are the other tools.**

Owner directive, 2026-09-26.
Verified against code on 2026-09-26 at `main` `04995db9`. The I-10 anchors
were verified against code on 2026-10-09 at `807be57cb`. One real ClickUp
export measured on 2026-09-27 (§4.1.1). Board row
**WS-41**. This spec records **D80**, which amends **D52.2**.

Where this spec and `work_plan.md` §2 disagree, the board wins. Where this spec
and `project_management_app.md` §7 disagree, this spec wins. §7 is superseded,
and this spec does not revive it.

---

## 0. One paragraph

A customer admin uploads the export file that ClickUp, Asana, Jira or another
tool already gives them. Metorite reads the file, shows what it found, and asks
the admin to confirm three mappings: people, statuses and the target space. Then
it writes the spaces, projects, tasks, subtasks and comments into the one task
store (`pm_*`). Metorite never connects to the other tool, and the file is the
whole contract.

ClickUp ships first. Every other tool is one more adapter behind the same
writer.

## 1. What the owner asked for

The owner, 2026-09-26, in three messages. The quotes are exact. The
paraphrase between them keeps each sentence inside the STE limit:

- Import "tasks, projects, and spaces from Clickup, Asana, Jira, or other
  popular applications". Roll them up into Metorite "without any issue".
- Start with ClickUp. "Then we can look at the other applications."
- "I don't need integration of the API." The owner wants to upload "some
  standard exports of those apps". "I don't need a direct connection with
  those apps."

Three requirements follow:

1. **File upload only.** No API, no OAuth, no stored credential, no sync.
2. **ClickUp first**, and complete. The other tools follow in the order of §9.
3. **"Without any issue"** means the import is honest. Before the write, the
   admin sees every count. The admin also sees each item the file could not
   carry, and each item the importer dropped, with the reason.

## 2. D80 — a file importer is allowed. D52.2 is amended

D52.2 deleted both ClickUp importers on 2026-08-24. It gave two reasons:

- "a live credential dependency", and
- "a second write path into `pm_tasks`".

D80 keeps both reasons and answers each one by construction:

| D52.2 objection | D80 answer | Fence |
|---|---|---|
| A live credential | The importer reads an uploaded file. It opens no network connection. It never fetches a URL that it finds in the file (§6.8) | `test_import_no_network.py` (new, §8) |
| A second write path | The importer writes through the same helpers that the Projects routes use: `insert_row`, `next_task_number`, `record_activity`, `apply_task_tags`, `land_custom_fields`. It adds one caller, not one path | `test_pm_task_insert_sites.py` (new, §8) |

**What D80 does NOT change.**

- D52.1 stands. There is no sync, in either direction. An import is one shot.
  A newer export updates tasks only when an admin uploads it (§6.9).
- D52.3 stands. The `clickup_*` columns stay unwritten. The importer records
  provenance in `pm_tasks.origin` (migration 211), not in `clickup_id`.
- D52.4 stands. Metorite is the system of record for project work. An import
  moves work in. It never makes the other tool a source of truth again.
- `tests/unit/test_no_task_provider_connectors.py` stays green. A file parser
  is not a connector.

**Edits D80 makes.** The spec PR changed the router lines on 2026-09-26: root
`CLAUDE.md` §2, root `AGENTS.md` constraint 8, `INDEX.md`, and
`project_management_app.md` §12.2. Two code edits wait for build slice I-2:

- The docstring of `apps/services/gateway/gateway/routes/projects/__init__.py`
  (lines 28-36) says "Do not re-add an importer here". It changes to name D80
  and this spec.
- `core.py:1305` names "both importers". It changes to name the file importer.

## 3. Scope and non-goals

### 3.1 In scope

- Upload one or more export files for one run.
- Parse ClickUp exports first (§5), then the tools in §9, one adapter each.
- A dry run that writes nothing and reports counts, warnings and losses.
- Three mappings the admin confirms: people, statuses, target.
- Spaces, folders, projects, tasks, subtasks, assignees, tags, priority,
  dates, estimates, custom fields, comments, checklists and dependencies,
  where the file carries them.
- A resumable apply, a final report, and a discard of one whole run.

### 3.2 Non-goals

- **Any live connection** to another tool. That includes attachment download
  from a URL in the file (§6.8).
- **Sync.** Nothing runs by itself, and nothing writes back to the source.
  An admin may upload a NEWER export of the same workspace. It updates the
  tasks an earlier import wrote, by the three-way rule of §6.9 (owner
  decision, 2026-09-28). That is an import the admin starts, not a sync.
- **Invitations.** An import never creates a member, never sends an invite and
  never sends mail. CLAUDE.md §3a rule 3 binds here.
- **Export to another tool.** `projects/export.py` owns CSV export.
- **Recurrence rules.** No export file carries a usable rule (§4.3). The
  importer writes the current instance and says so.
- **Time entries.** Metorite has no time-entry table. §6.6 says where the
  total goes.
- **Docs, whiteboards, chat, dashboards, automations and forms** of the source
  tool.
- **Basecamp.** Its only export is an HTML archive. §9 records why.

## 4. What the files carry (research, 2026-09-26)

The research used vendor help pages where they loaded. `help.clickup.com` and
`support.monday.com` refused the fetcher (HTTP 403). The facts for those two
come from search snippets of the official pages and from secondary write-ups.
The tag **[unverified]** marks each fact that no official page confirmed.
**Rule: the parser is built from a real file, not from this table.** §10 P-1
makes that the first gate.

### 4.1 ClickUp

Hierarchy: Workspace → Space → Folder (optional, can nest) → List → Task →
Subtask (can nest). A List can sit in a Space with no Folder. One task can
live in several Lists.

ClickUp gives two exports, and **no single file carries everything**:

| | **A. Workspace export** | **B. View export** |
|---|---|---|
| Menu | Avatar → Settings → Imports / Exports → Export Items → select locations | A List or Table view → ⋯ → Export view → "All columns" |
| Format | CSV | CSV or XLSX |
| Who may run it | Owners and Admins. Members only with the Exporting permission. Guests never | Members with Edit access and the Export Views permission |
| Plan limit | None found | Free and Unlimited: **5 view exports for the life of the workspace**. Business and above: unlimited [unverified] |
| Scope | Chosen Spaces, Folders or Lists | One view, with its filters |
| Hierarchy | Space Name, Folder Name (a path when nested), List Name, Parent ID, Subtask IDs. **Names only — no container ids** | The view's own List |
| Comments | Yes, one cell per task, structured text [unverified shape] | No |
| Checklists | Yes, one cell per task [unverified shape] | No |
| Custom fields | **No** | **Yes** |
| Attachments | A cell of file names and URLs [unverified shape] | No |
| Dates | Twice: epoch milliseconds, and a "… Text" twin in the exporter's local time | A format the exporter picks |
| Closed tasks | [unverified] | Yes, with "Visible columns" |
| Download link | Expires after 1 hour | Expires after 1 hour |

Where the table above and §4.1.1 disagree, §4.1.1 wins. It is measured.

#### 4.1.1 Measured — one real workspace export (2026-09-27)

The owner supplied one real workspace export on 2026-09-27. It holds 2,689
rows for **2,423 distinct tasks** (fact 1). It has 34 columns, 5 Spaces, 9 Folders and 48 Lists, created from 2025-09-12
to 2026-09-25. It is UTF-8 with no BOM and LF line ends.

⚠️ **The file holds real staff names, real emails and real task text. It
never enters the repo.** The committed fixture is a scrubbed copy (§9 P-1).

**The 34 columns, in file order:**

| # | Column | Measured content |
|---|---|---|
| 0 | `Task ID` | A short ClickUp id, for example `86a1bcdef` |
| 1 | `Task Link` | `https://app.clickup.com/t/<Task ID>` |
| 2 | `Task Type` | `Task` in every row |
| 3 | `Task Name` | Plain text |
| 4 | `Task Content` | Plain text. 447 tasks hold one. A few hold `<` or `**` |
| 5 | `Status` | The status name, as the List spells it |
| 6, 7 | `Date Created`, `Date Created Text` | Epoch milliseconds, and `9/26/2026, 3:04:05 pm GMT+5:30` |
| 8, 9 | `Due Date`, `Due Date Text` | The same pair. 1,091 tasks hold one |
| 10, 11 | `Start Date`, `Start Date Text` | The same pair. 15 rows hold one |
| 12 | `Parent ID` | A Task ID, or the literal text `null` |
| 13 | `Subtasks IDs` | Task IDs joined by `,` with no space |
| 14 | `Attachments` | JSON: `[{"title": ..., "url": ...}]`. 15 tasks hold one or more |
| 15 | `Assignees` | `[Name One,Name Two]`. Display names, and an email when the person has no name |
| 16 | `Tags` | `[tag one,tag two]` |
| 17 | `Priority` | `1`, `2`, `3`, `4`, or the text `null` |
| 18 | `List Name` | The List |
| 19 | `Folder Name/Path` | JSON: `["Folder"]` or `["Parent/Child"]`. Empty for a List with no Folder |
| 20 | `Space Name` | The Space |
| 21, 22 | `Time Estimated`, `Time Estimated Text` | Milliseconds, and `40 h` |
| 23 | `Checklists` | JSON: `{"Checklist name": ["item", "item"]}` |
| 24 | `Comments` | JSON: `[{"text", "by", "date", "assigned", "resolved"}]`. `by` is an **email** |
| 25 | `Assigned Comments` | `0` in every row |
| 26, 27 | `Time Spent`, `Time Spent Text` | ` "1569"` with a leading space and quotes, in milliseconds, and `0.03 m` |
| 28, 29 | `Rolled Up Time`, `Rolled Up Time Text` | `NaN`, `null` or a number |
| 30 | `Home Location ID` | **The List id.** 48 distinct values, one per List |
| 31 | `Home Location` | `Space > Folder > List`, or `Space > List` |
| 32, 33 | `Other Location IDs`, `Other Locations` | Empty in every row of this file |

**What the file does NOT hold:**

- **No custom fields.** No column carries one.
- **No completion date.** 1,647 tasks sit in a closed or done status. No
  column says when they closed. §6.6 and §11 Q-6 deal with this.
- **No status type.** Only the name.
- **No checked state on a checklist item.** An item is text only.
- **No Space id and no Folder id.** Only the List has an id.

**Twelve measured facts the parser must meet:**

1. **A task can appear twice.** 266 Task IDs sit on two rows each, and no id
   sits on three. The two rows differ only in `Assignees` (5 ids) or
   `Subtasks IDs` (3 ids). The parser merges them into one task and takes
   the union of both lists. The dry run reports the count.
2. **`Parent ID` is the truth, not `Subtasks IDs`.** The two disagree on
   182 tasks. On 166 of them, `Subtasks IDs` is empty while the task has
   children. The parser builds the tree from `Parent ID` alone.
3. **Subtasks nest four deep.** Depth counts: 1,140 at the top, then 872,
   317, 81 and 13. `pm_tasks.parent_task_id` has no depth cap, so every
   level lands.
4. **Some parents are not in the file.** 13 rows name a parent that no row
   holds. The parser lands each one at the top of its List and reports it.
5. **A subtask always shares its parent's List.** No row broke this.
6. **`null` is text.** `Parent ID` and `Priority` write the four letters
   `null`. The parser reads them as empty.
7. **Priority is a number.** 1 is Urgent and 4 is Low. §6.4 maps it.
8. **The file names its own time zone.** Every `… Text` column ends with the
   exporter's offset, for example `GMT+5:30`. The parser reads the zone from
   the file and does not ask the admin. **Each row carries its own offset.**
   A zone with summer time writes two offsets in one file. So the parser
   reads each date's offset from that date's own `… Text` twin. A zero
   offset has no sign: the text ends in a bare `GMT`, which reads as +0.
9. **A due date with no time sits at 04:00 local.** 1,075 of the 1,091 due
   dates fall at exactly 04:00 in the file's zone. ClickUp writes a date with
   no time that way. The parser lands 04:00 local as a date only (§4.3 item
   1). Any other time lands as a real time. "Local" means the offset of that
   row (fact 8), never one offset for the whole file.
10. **Assignees are names. Comment authors are emails.** The people screen
    (§6.2) therefore has an email for everyone who wrote a comment, and a
    name for everyone else.
11. **Status names differ in case and spelling between Lists.** This file
    holds `Closed`, `done`, `completed`, `to do`, `todo`, `in process`,
    `in progress`, `backlog`, `review` and `on hold`. Each List keeps its
    own set, and 48 Lists hold 29 different sets. The file shows only the
    statuses that some task uses, not the whole set of the List. 7 Lists
    show no done-like status, so D79 adds one to each (§6.3).
12. **The comment date has no epoch twin.** It is text only, in the form
    `9/26/2026, 3:04:05 pm GMT+5:30`, month first. The parser reads the
    offset from the string.

**Hazards that remain:**

1. **Custom fields are in a second file.** The admin may add one view export
   per List. The importer joins it to the workspace export on Task ID.
2. **Collapsed groups drop out of a view export** [unverified]. The dry run
   compares task counts per List across the two files and warns on a gap.
3. **Large workspace exports can fail at ClickUp.** The wizard tells the admin
   to export one Space at a time. A run accepts several files.
4. **One task in several Lists.** `Other Location IDs` carries it, and this
   file holds none. The parser keeps the home List and reports the others.
5. **Work already in Metorite.** Customer zero ran on ClickUp until D52, and
   the deleted importer wrote `pm_tasks.clickup_id`. The same Task ID can
   therefore exist in Projects already. §6.9 and §11 Q-7 deal with this.

### 4.2 The other tools, in brief

| Tool | Best file | Hierarchy in the file | People | Comments | Main loss |
|---|---|---|---|---|---|
| Asana | Project CSV (any member) or project JSON | Project, Section, Parent task (by name) | Name, and an Assignee Email column [unverified] | No | Comments, attachments |
| Jira Cloud | Issue search → CSV (all fields), 10,000 rows per file | Project, Parent, Epic | Display name or account id, no email | Yes, one repeated `Comment` column each | Attachment files (links need a Jira login) |
| Trello | Board JSON (any member) | Board, List, Card, Checklist | Name and username, no email | Last 1,000 actions only | Older comments |
| Linear | Workspace CSV (admins) | Team, Project, Parent issue | Name | No | Comments |
| Todoist | Project CSV (any user) | Row order plus `INDENT` 1–4 | `Name (id)` | Yes, as `note` rows | Completed tasks |
| monday.com | Board → Export to Excel, or the account ZIP (admins) | Group header rows, subitem rows | Name [unverified] | Updates tab, not subitem updates | Subitem updates |
| MS Planner | Export plan to Excel | Plan, Bucket | Name [unverified] | No | Comments, checklist items (counts only) |
| Notion | Markdown & CSV ZIP | One CSV per database, one `.md` per row | Name | No | Comments, status group |
| Wrike | Account backup ZIP of JSON (paid admins) | `folders.json`, `tasks.json` | **Email**, in `users.json` | Yes | Recycle bin |
| Smartsheet | Excel export, or Request Backup | Excel outline rows | Contact text | Separate tab | Attachments (Excel) |
| Basecamp | HTML archive only | HTML | HTML | HTML | Everything structured |

### 4.3 Hazards common to every tool

1. **Time.** Formats differ: epoch milliseconds (ClickUp), ISO 8601 UTC
   (Trello, Asana JSON), locale strings (Jira), prose (Notion), natural
   language (Todoist). When the file names no zone, the wizard asks for the
   exporter's zone. The ClickUp file names it (§4.1.1 fact 8). The parser
   never reads a text date when an epoch column exists. A date with no time
   stays a date. It lands in `start_date`, or at LOCAL NOON of that day in
   `due_at`. The app writes a picked day at that instant
   (`quickAdd.ts` `dueInstantForDay`). So an imported date and a typed date
   read the same.
2. **Rich text.** Markdown, plain text, HTML and Jira wiki text all occur.
   The target is Markdown, because `pm_tasks.description` is Markdown. HTML is
   stripped to text and never stored raw.
3. **People.** Only the Wrike backup reliably gives an email. Every other
   file gives a name. §6.2 is the mapping screen.
4. **Status.** Only Jira (category), Linear (type) and Wrike (group) carry a
   done meaning in the file. §6.3 is the mapping screen.
5. **Priority.** The scales disagree, and Todoist inverts its own scale
   between CSV and API. The importer maps by **name**, never by number.
6. **Multi-value cells.** Jira repeats a column header. Other tools join
   values with commas inside one cell. The CSV reader must keep duplicate
   headers, and must not split a name that holds a comma.
7. **One task in many containers.** ClickUp, Asana and Wrike write it as two
   rows. The importer deduplicates by source id and keeps the first home. The
   report lists the other homes.
8. **Recurrence.** No file carries a usable rule. The importer writes the
   current instance and adds a report line per recurring task it can detect.
9. **Archived and closed items.** Each tool includes or omits them in its own
   way. The dry run prints the counts, so the admin can see a gap.
10. **Attachment URLs.** Some are public and never expire [unverified for
    ClickUp]. That is a data-exposure fact about the source tool. §6.8 keeps
    the names and never stores the URL.
11. **Encoding.** Excel re-saves CSV as cp1252. The reader detects a BOM,
    tries UTF-8, and falls back to cp1252. It reports which one it used.

## 5. The design

### 5.1 The pipeline

```
upload ──► parse (adapter) ──► ImportBundle ──► plan (dry run) ──► admin maps
                                                                      │
report ◄── apply (one writer, batched, resumable) ◄──────── confirm ──┘
```

- **An adapter** knows one tool's file. It turns files into one
  `ImportBundle`. It never touches the database.
- **The bundle** is the one canonical model (§5.2). The writer knows only the
  bundle. A new tool is one new adapter plus its fixtures.
- **The plan** is a pure function of the bundle and the mappings. It returns
  the counts, the warnings and the losses. It writes nothing.
- **The writer** is the only code that writes. It reuses the core helpers
  (§2). It runs in batches under `tenant_session(org_id)`.

### 5.2 `ImportBundle` — the canonical model

One Pydantic model in `apps/services/gateway/gateway/routes/projects/importer/bundle.py`:

The table is the model as built in I-1. `bundle.py` is the source of truth.

| Entity | Fields |
|---|---|
| `Container` | `ref` (source id, or the name path when the file has no id), `kind` (space, folder or project), `name`, `parent_ref`, `source_id` |
| `Person` | `ref`, `display_name`, `email` (optional) |
| `StatusSeen` | `container_ref`, `name`, `task_count`, `done_hint` (true, false or unknown) |
| `FieldDef` | `container_ref`, `name`, `type_guess`, `options` — not built yet. I-5 adds it with the view export |
| `Task` | `ref`, `container_ref`, `parent_ref`, `title`, `description_md`, `status_name`, `task_type`, `importance`, `assignee_refs`, `tags`, `created_at`, `start_date`, `due_at`, `due_date`, `completed_at`, `estimate_mins`, `time_spent_mins`, `custom_values`, `checklists`, `attachment_names`, `blocks_refs`, `custom_id`, `url` |
| `Checklist` | `name`, `items` |
| `Comment` | `task_ref`, `author_ref`, `created_at`, `body_md` |
| `Loss` | `what`, `count`, `why` — one row per thing the file could not carry |
| `BundleWarning` | `code`, `message`, `count`, `sample_refs` — one row per defect in this file |

Three choices differ from the first draft of this table:

- **`importance`, not `priority_name`.** The adapter maps priority by the
  source tool's own meaning (§6.4), so the bundle holds Metorite's 0–3 value.
- **`due_at` or `due_date`.** A due date with no time is a date (§4.3 item
  1). At most one of the two is set.
- **Every column has a stated fate.** `clickup.COLUMNS` marks each column
  as read, or says why the adapter skips it. A test fails if a column of the
  real file has no entry.

A value the file does not carry is `None`, never a guess. The adapter writes
a `Loss` row for each whole field that its tool never exports. An example is
"ClickUp workspace export: custom fields — no column".

### 5.3 Where the rows land

**Target.** The admin picks one target per run:

- **A new space** (the default). The name defaults to the source space name.
- **An existing space** that the admin can see. The imported tree lands under
  it. The grammar allows ONE folder between a space and a project, so a source
  Space and its Folder flatten into one folder named "Space / Folder". A List
  with no Folder lands in a folder named after its Space.

With a new space, the admin's name replaces the source Space's name only when
the file holds one Space. A file of five Spaces makes five spaces, each with
its own name.

**Hierarchy (ClickUp).** It fits the Projects tree grammar
(`core.assert_node_grammar`, `core.py:465`). The depth cap is 3.

| ClickUp | Metorite | Notes |
|---|---|---|
| Space | Space (a root `pm_projects` row) | Or the chosen existing space |
| Folder | Folder (`kind='folder'`) | A nested Folder flattens to one folder named "Parent / Child" |
| List | Project | Under its folder, or under the space when folderless |
| Task | `pm_tasks` row | `root_project_id` and `task_number` come from the helpers |
| Subtask | `pm_tasks` row with `parent_task_id` | **The old importer landed subtasks at the top level. This one keeps the parent** |

**Grants.** A new space gets the same `org` grant that `create_node` gives
(`tree.py:833-842`). The admin can narrow it before apply to a
`group:<slug>`, on the wizard's Spaces step (I-8), from the organization's
groups (`GET /admin/groups`). The importer never writes an email grant. It never proposes a
grant either. D-PM-10 (`project_management_app.md` §8) keeps a bulk grant
the act of a person, not of the software.

**Organization.** It comes from the admin's session through
`require_organization(vis)`, never from the upload or the request body
(R5(e)). Children take it from the trigger `pm_organization_from_parent`.

## 6. The mappings, field by field

### 6.1 Provenance and idempotency

Every imported task carries this in `pm_tasks.origin`:

```json
{"kind": "import", "source": "clickup", "run_id": "<uuid>",
 "external_id": "86a1b2c3", "custom_id": "ENG-42", "url": "https://..."}
```

- `pm_tasks.source` is `'import'`. The CHECK already allows it (`146`).
- A new partial unique index holds one row per
  `(organization_id, origin->>'source', origin->>'external_id')` where
  `origin->>'kind' = 'import'`. This makes a re-run safe (§6.9).
- `created_at` keeps the source date. The ClickUp file names no creator, so
  `created_by` is `system:import:clickup`. A comment's `created_by` is its
  mapped author, else the same system actor.

### 6.2 People

The mapping screen lists every `Person` in the bundle, with the count of tasks
each one holds.

1. The importer proposes a match. It tries the email first, then an exact,
   case-insensitive full name against the `people` directory **of this
   organization only**.
2. The admin confirms, changes or clears each match.
3. The picker offers only members of this organization. This closes the
   cross-tenant hazard at `core.py:1573-1579`. `set_assignees` does no
   membership check of its own (`tasks.py:1010-1040`).
4. A person with no match is **not** invited and **not** created. Their tasks
   land unassigned. The name goes into `origin.assignee_names`, and the
   description gets one footer line: "Assigned in ClickUp to: Priya Rao".
5. **The gap closes later (I-9).** Add the person to People, then upload the
   same export again. A task no member has changed gets its people, and the
   footer line drops the names that now match. A mapping change never takes
   anybody OFF a task.

### 6.3 Statuses

Metorite keeps a status set on the nearest node with `owns_statuses`
(migration 196). ClickUp keeps one per List.

⚠️ **I-10 replaces the first rule below. Owner decisions, 2026-10-09.** The
owner saw the Map step and asked why an admin can only map a status, and
not create one. Each row already created a status, but the screen did not say so.
The owner chose three things:

1. **One status set per space.** A new space owns one set, and every List
   that the run creates uses it. A List keeps its own set only when an admin
   chooses that later, in Settings.
2. **The step opens on a summary.** It shows the statuses that the import
   will make, with merges already proposed. One button opens the rows.
3. **Rules now, Jev later.** Name rules make the proposals. An AI proposal
   for a name that the rules cannot place is a later slice (I-13).

**The I-10 model.**

- **The target set.** A new space starts with the root seed of
  `tree._seed_root`: Backlog, To do, In progress and Done. An existing-space
  target uses the set of that node's status owner (`core.status_owner_id`).
  The route reads that set into the plan's facts, as it reads the people
  directory, so the plan stays pure.
- **A run that continues reads the real sets** (the I-10 review, P2-b). It
  plans against the sets of the spaces it goes into again, never against
  the seed alone. So a lane that the earlier run added does not show as
  "new". The seed joins only when the run also makes a new space. Live
  check 4.2b holds it.
- **A lane that a member renamed keeps its tasks.** Each run records the
  status ids of each set it used. A run that continues follows a lane
  through those ids. The plan shows the new name when every such lane
  carries one new name (`import_writer.continuation_facts`). The writer
  reads the ids of ALL earlier Lists for every List, and also for a List it
  creates now (P1-a). So it never adds the old name back as a second lane.
  Live checks 9.1, 9.2 and 9.3 hold it.
- **Each ClickUp status becomes one target status.** It is a status that the
  target set already holds, or a new status with a name and a stage. The
  mapping shape does not change: `StatusChoice` holds a name and a category.
  A name that matches a status in the target set, case-blind, takes the
  stage of that status, and the screen shows that stage as fixed.
- **Two ClickUp statuses with one target merge**, as before.
- **The writer has ONE way to add a status to a space's set:
  `_reuse_statuses`.** It matches names case-blind and reads the set again on
  each call. `pm_task_statuses` holds `UNIQUE (project_id, name)` with case
  (146_projects.sql), so a second insert loop after `_seed_root` would
  collide with the seed's "Done", and a constraint error is not retried
  (§8). A new name goes in after the last status of its stage. On the
  Fracktal export, Review lands after In progress and On hold after Backlog.
- **Every List and Folder that the run creates sets `owns_statuses =
  false`**, so it uses the space's set. Each List's entry in the progress
  map (`progress["statuses"]`) is the space's set.
- **A task with no status takes the first status of the set by position.**
  `core.load_default_status` names it, and it skips a triage lane. In the
  seed it is Backlog. The writer never reads `is_default`, because the owner
  retired that flag for statuses on 2026-09-06. Live check 8.1 holds it.
- **D79, per set.** A set that holds any done-stage status gains nothing.
  `layout.project_statuses` stops adding a "Done" to each List that uses the
  space's set. `done_status_added` counts the SETS that gained a Done. That
  is 0 for a new space, because the seed holds Done.
- **`lanes_added` counts lanes added to a set that this run did not
  create.** The names a run adds to its own new space do not count. The
  report line (`importFlow.reportLines`) says "statuses added to spaces and
  lists that were already in Metorite".
- **Continuity (§6.9).** A List that an earlier run made, and that owns its
  set, keeps that set. The run adds a missing name to it, as before. Only a
  List that this run creates uses the space's set.
- **A run that continues an earlier import keeps that import's names.** The
  proposal applies only to a run that starts a new tree. On a run that
  continues one (`plan.continues`), the inherited mapping applies as before
  I-10: a choice with `name = null` keeps the ClickUp name, and a status with
  no choice keeps its ClickUp name too. A synonym fold would move tasks from
  "in process" to a new "In progress" lane, which is the
  opposite of continuity. A live check holds it: a re-upload of a pre-I-10
  tree adds 0 lanes.
- **How a run knows that import's names (built in I-10).** Each run records
  the status that each source name became, in `progress.status_names`. A run
  that continues takes that status for a source name with no named choice.
  A run written before I-10 recorded none. Its names were the source names,
  so the source name stays, as the rule above says. The record is necessary
  because a re-run of an I-10 tree can come with no choices. Then the
  source name "in process" would add a new lane beside "In progress".
  Writer check 4.2 fails without the record, and check 7.2 fails without
  the rule above.
- **What a null name means on a new tree.** The I-10 wizard sends the target
  name on every row, so `name = null` comes only from a pre-I-10 mapping.
  On a new tree, a null name and a missing choice both take the proposal.
- **An import never lands a task in an intake lane** (the I-10 review,
  P1-b). An existing space can hold the intake lane "Triage", in the
  category `triage` (`core.TRIAGE_CATEGORY`). Lists and boards hide a task in
  that lane. So the writer never matches a triage lane and never adds a task
  to one. The name is still taken, because `UNIQUE (project_id, name)` holds
  it. Decision: a target with the name of an intake lane, proposed or
  chosen, becomes a new status "<name> (imported)". The plan and the Map
  step show that name. Live check 8.2 holds it.
- **The writer applies the same rule in each set it writes into** (the
  fix-round follow-up). The plan cannot see every intake lane, for example
  in a List that owns its set. So the writer puts the tasks into
  "<name> (imported)" in any set that holds an intake lane with that name,
  and it never stops the run for it. Live checks 10.1 to 10.3 hold it.
- **A seed status that no ClickUp status maps to stays.** It holds no task.
- **The proposal.** The screen proposes a target for each name. The table
  is case-blind and folds runs of spaces:

  | ClickUp name | Proposed target |
  |---|---|
  | done, closed, complete, completed, finished, resolved, shipped | Done |
  | to do, todo, open, new, not started | To do |
  | in progress, in process, doing, wip | In progress |
  | backlog | Backlog |
  | cancelled, canceled, won't do, wont do | Cancelled, a new status in the cancelled stage |
  | anything else | A new status with the same name and a capital first letter. Its stage comes from `plan._PROPOSALS`, which is the source of record for the stage rules. The table under the replaced rule below is a summary of it |

  On the real Fracktal export, the ten ClickUp statuses become six: Backlog,
  To do, In progress, Review, On hold and Done.

**The rule that I-10 replaces**, kept for the record:

- **Each imported project owns its statuses** (`owns_statuses = true`). Its
  set is the status names the file shows for that List, in first-seen order.
- The admin sets the **category** of each name: backlog, todo, in_progress,
  done or cancelled. The screen proposes one from the name, case-blind:

  | Name holds | Proposed category |
  |---|---|
  | done, complete, closed, shipped, resolved | done |
  | cancel, won't, wont | cancelled |
  | backlog, on hold, hold, someday | backlog |
  | to do, todo, open, new | todo |
  | anything else, for example "in process" or "review" | in_progress |

- **One name, one status, across the run.** The real file spells one idea
  several ways, for example `to do` and `todo`. The screen lists each
  distinct name once, with its task count. The admin may merge two names into
  one status before apply.
- **D79 binds.** A set with no done status gets a "Done" status added, and the
  report says so.
- A task in a done or cancelled status needs `completed_at`. The ClickUp file
  has no completion date (§4.1.1). §6.6 sets the rule.

### 6.4 Priority

`pm_tasks.importance` is 0–3, and Important means 2 or more (D78, migration
218). Urgent is derived from `due_at` and is never stored.

| ClickUp `Priority` | Name in ClickUp | `importance` | Tasks in the real file |
|---|---|---|---|
| `1` | Urgent | 3 | 53 |
| `2` | High | 2 | 212 |
| `3` | Normal | 1 | 199 |
| `4` | Low | 0 | 1 |
| `null` | none | the column default | 1,958 |

The ClickUp file writes the number, not the name (§4.1.1 fact 7). Each
later adapter adds its own rows to this table. **An adapter maps by the
source tool's own meaning, never by a shared number.** Todoist writes 1 for
the highest priority in its CSV, and 4 for the highest in its API.

The importer never writes `leveraged`.

### 6.5 Tags, types and custom fields

⚠️ **I-11 changes where tags and types land. Owner directive, 2026-10-09:**
the import must bring in tags and the other task data too, and it must fill
the shared vocabulary from the file in a suitable way. Migration 175 (WS-27bj) holds the shared vocabulary.
A row with `project_id IS NULL` belongs to the whole organization. On
2026-10-09, Fracktal held no org-wide tag, type or field, and
`PROJECTS_ORG_VOCABULARIES` was on in production.

**The I-11 model.**

- **A ClickUp tag becomes a shared tag.** A tag that matches an existing tag
  of the organization or of the target space, case-blind, uses that tag. A
  new tag is registered org-wide through the `organization_id` argument of
  the tag helper in `tags.py`. Two ClickUp tags that differ only in case
  merge into one.
- **The admin may change each tag**: use an existing tag, make a new shared
  tag, or leave it out. A tag left out is counted in the report.
- **A task type follows the same rule.** A name that matches a type of the
  root or of the organization uses it. A new type is created org-wide. The
  Epic rule below does not change.
- **The fallback.** An org-wide row needs `admin:settings:manage` and the
  flag (`core.ORG_VOCABULARY_WRITE`, `org_vocabularies_enabled`). The import
  needs only `admin:access:manage`. An admin who lacks the first, or a box
  with the flag off, gets the tags and types on the target SPACE, never on
  each List. The step says which one applies before the import.
- **The caps.** `tags.py` counts the effective set, which is the space's
  tags and the org-wide tags together, against `MAX_TAGS_PER_PROJECT`
  (500). The import uses the same count, and a tag over the cap is dropped
  and counted, as before.
- **Continuity (§6.9).** A tag that an earlier run wrote on a List stays on
  its tasks. The I-11 build checks how the update rule compares tags, and
  records here whether a shared tag of the same name counts as no change.
- **The step.** The Map step adds "Tags and types", which opens on a summary,
  for example "3 ClickUp tags become 3 new shared tags. The type Task
  matches the existing type Task." One button opens the rows. Each row has
  one "Becomes" picker, as in §7.7.

**The rules below still bind, except where the I-11 model replaces them.**

- **Tags** go through `apply_task_tags`, scoped to the imported project.
  The shared caps of `tags.py` bind: 64 characters a tag, 25 tags a task, 500
  tags a space. A tag that does not fit is DROPPED and counted as
  `tags_dropped` in the report. It never fails the run (built in I-3).
- **Task Type** (ClickUp) maps by name to a `pm_task_types` row of the root,
  and creates the type when it is missing. An Epic has no parent (§3.4 of
  `project_management_app.md`). So a subtask whose source type is "Epic"
  keeps its parent and takes the default type. The report counts it as
  `epic_demoted`.
- **Custom fields** come from the view export. Each field becomes a
  `pm_custom_fields` row on its project. The `field_key` is the slug of the
  name, which must match `^[a-z][a-z0-9_]{0,62}$`. The type guess:

  | Source value | `field_type` |
  |---|---|
  | Every value a number | number |
  | Every value a date | date |
  | At most 50 distinct short values | select, or multi_select when a cell holds several |
  | `http://` or `https://` | url |
  | true/false, yes/no | boolean |
  | Anything else | text |

  The admin can change a type on the plan screen. Values land through
  `land_custom_fields`. The importer never sets `required`.

### 6.5b Columns the importer does not read (I-8)

A ClickUp export can carry columns this importer does not know, most often
custom fields. The adapter keeps each one's non-empty values on the task
(`Task.extra_columns`, 500 characters a cell), and the plan lists each column
with the count of tasks that fill it and up to three examples.

The admin picks, per column:

- **Leave out** — the default, and what every run before I-8 did.
- **Keep in the description** — each task that has a value gets a block at
  the end of its description, "**More from ClickUp**", one line a column.

A kept column never becomes a custom field. That is I-5, which needs the view
export for the types.

### 6.6 Dates, estimates and time

- `due_at`, `start_date` and `created_at` map as named, from the epoch
  column. A due time of exactly 04:00 in the file's zone is a date with no
  time (§4.1.1 fact 9).
- **`completed_at` has no source in the ClickUp file.** 1,647 of 2,423 real
  tasks are closed. The rule, pending §11 Q-6: take the latest date the task
  carries — a past due date, the last comment, or the creation date. Set
  `origin.completed_at_estimated = true`. **Never use the import time.** It
  would put 1,647 completions into one day of every report and chart. The one
  exception is a closed task that carries no date at all. A ClickUp export
  never holds one, because every row has `Date Created`.
- `estimate_mins` takes `Time Estimated`, which is in milliseconds.
- `Time Spent` has no home, because there is no time-entry table. The value
  is in milliseconds, inside quotes and after a space. It goes into
  `origin.time_spent_mins`, and the report says the total is kept but not
  shown.

### 6.7 Comments and checklists

- **Comments** become `pm_activities` rows of type `comment`, with the source
  date. The ClickUp file gives the author as an email, so the match is
  exact when that person is a member. Otherwise the author is
  `system:import:<source>` with `meta.author_name` set. Replies stay flat.
  The real file holds 93 comments on 61 tasks.
- **Checklists** have no table. Each checklist becomes a Markdown list at the
  end of the description, under its own name. The ClickUp file does not say
  which items are ticked, so the list has no tick boxes:

  ```markdown
  **Launch checklist**
  - Book the venue
  - Send the invites
  ```

  A tick box that always shows empty would state something false about a
  closed task. The alternative is one subtask per item. §11 Q-3 asks the
  owner. The real file holds 45 checklists, and 18 of them hold items.

### 6.8 Attachments

**Phase 1 keeps the file names and never the files.** The description gets a
footer that lists each name, and the report counts them.

The URL is not stored. A ClickUp URL can be public and permanent [unverified].
A stored URL then puts a public link to the customer's file into our database.
A download needs an outbound fetch of a URL taken from customer input, which
is a server-side request forgery risk. It is also a live connection, which the
owner excluded. §11 Q-1 asks whether a later slice accepts the files
themselves, uploaded as a ZIP.

### 6.9 Dependencies, re-runs and discard

- **Dependencies.** "Blocks" becomes a `pm_task_links` row of type `blocks`,
  written after all tasks exist. A link to a task outside the run is a `Loss`.
- **Work that the old importer already wrote.** Before D52, the deleted
  ClickUp importer wrote `pm_tasks.clickup_id`. The dry run counts the tasks
  in the file whose Task ID matches a `clickup_id` in this organization. By
  default the apply skips them, and the report names the count. This is a
  read of a D52.3 column, which D52.3 retired from reading. §11 Q-7 asks the
  owner to allow this one read.
- **Re-run: the tasks UPDATE (owner decision, 2026-09-28, §11 Q-5).** The
  owner asked: "if I upload a new export, it can update the tasks as well".
  A task an earlier import wrote is updated from the new export, field by
  field, by one three-way rule (`layout.merge_fields`):

  | The source changed the field? | A member changed it in Metorite? | Result |
  |---|---|---|
  | No | either | Nothing changes |
  | Yes | No | The source's value is written |
  | Yes | Yes | The member's value is KEPT, and the report counts a conflict |

  The fields: title, description, status (the completion date follows it),
  due date, start date, priority, estimate, tags and assignees.

  **Two snapshots, two questions (built after the I-3b review).**

  - `origin.import_source` holds the SOURCE's own values: the status NAME,
    the person refs, and the description without the "Assigned in ClickUp
    to" footer. "Did the source change this field?" is answered here. So a
    different people or status mapping on a later run changes nothing by
    itself: it is not a change in ClickUp.
  - `origin.import_values` holds what Metorite got from the import. "Did a
    member change this field?" is answered here.

  **The rest of the rule.**

  - A new upload of a workspace an earlier import wrote STARTS from that
    import's mapping, so the admin's hand-made choices carry over. A person
    who has left the organization falls back to the proposal.
  - A status lane a member renamed is followed through the status ids the
    earlier run recorded. The old name does not come back as a new lane.
  - A task whose project differs from its List keeps its status and its
    assignees. A member may have moved it into a personal project, and a new
    assignee there would widen who can read it. The report counts it as
    `tasks_moved`.
  - A task that did not change keeps its `updated_at`. That value is the
    If-Match token (D-PM-20) and the cursor of the delta feed. Only the
    bookkeeping in `origin` is written, with `touch=False`.
  - `origin.run_id` stays the run that CREATED the task. The last run that
    looked at it is `origin.updated_by_run`.
  - A tag enters the registry only when the merge writes it. A task type, a
    detach and a dropped tag are counted for new tasks only.
  - A source comment not yet imported is added once (`meta.import.comment_key`).
    A comment written before keys existed is matched on its date and text.
  - A task new in the export is created in the same tree. An update never
    moves a task: its parent and its project stay.
  - Each updated task gets one `system` activity naming the fields, and
    nobody is notified. The pre-D52 importer's rows are still skipped: they
    hold no snapshot to merge with.
- **How the skip works (built in I-3).** Each batch first reads which of its
  tasks exist (the import origin, and `clickup_id` for the old importer's
  rows). It writes a NEW task through `insert_row`, the helper every route
  uses, and updates an earlier import's task through `update_row` (§6.9).
  The unique index of migration 219 is the backstop: a write it refuses fails
  the batch, and the batch rolls back whole. There is no `ON CONFLICT` in the
  writer, so no conflict can drop a task in silence.
- **Nodes are reused (built in I-3).** An earlier run of the same source
  may have written a space, folder or project for the SAME target. Batch 0
  reuses that node when it still exists and is not archived. A re-run therefore creates
  nothing.
- **Reuse needs a real continuation.** Space and Folder refs are NAMES. So
  a different workspace with a Space called "Team Space" must never land in
  an earlier import's "Team Space". A run continues an earlier run only
  when it is the same export. The file hashes match, or the earlier run
  already wrote a task of this file. The target, the grant and the admin's space
  name must also be the same. A reused node must still sit under the parent
  this run resolved. A node moved since is created again, so its root never
  comes from an old map. The report names the runs it continued
  (`continued_from`).
- **A reused status set.** A name the set already holds keeps ITS stage, and
  the task takes that stage, completion date included. A new name goes after
  the last status of its own stage.
- **What reuse gives.** A re-upload after a failure continues in the same
  tree, so its subtasks keep their parents. A new-space run reuses only
  earlier new-space runs. An existing-space run reuses only earlier runs
  into that same space. The report counts reused nodes as `created.reused`.
- **A parent in another tree.** A subtask whose parent the old importer wrote
  lands at the top of its project, and the report counts it as
  `subtasks_detached`. A parent link never crosses from one space to another.
- **Discard (I-6, amended 2026-09-28 for update mode and reuse).** The
  wizard lists this organization's recent imports. A `done` or `failed` run
  offers "Discard this import" for 14 days after it ends. An open run
  (`uploaded`, `planned`) is discarded with no undo, because it wrote nothing.
  A run that is `applying` is refused: wait for it, or resume it.

  **What a discard removes.** Only rows THIS run wrote:

  - the tasks it created (`origin.run_id`), with their subtasks, comments,
    assignees and links, through the foreign keys;
  - the comments and the activity it added to a task an earlier run created
    (`meta.import.run_id`);
  - the spaces, folders and lists it created. The writer records them in
    `progress.created_nodes` from I-6 on. A reused node is never deleted.

  The deletes are plain `DELETE`s, as `DELETE /nodes/{id}` does. So migration
  168's trigger writes the tombstones, and the delta feed sees the tasks go.
  Nobody is notified, and no event fires (§6.10).

  **What a discard cannot undo.** A field this run changed on a task an
  earlier import created stays changed, because the earlier value is not
  kept. The result counts these as `updates_kept`. A status lane, a task type
  or a tag that this run added to a space it did not create stays too.

  **When it refuses.** It deletes nothing, and it names up to 10 of the
  tasks or nodes that stop it:

  1. A later import built on this one: a run that is not discarded continued
     this run's nodes, or updated or commented on a task this run created.
     Discard the later run first.
  2. A member worked on a task this run created. Its `updated_at` is later
     than the end of the last import that wrote it, or it has a comment or
     activity that no import wrote. A personal triage, an attachment or a
     board position counts, and so does a watch or a link made after the
     run.
  3. A member added work inside a node this run created: a task or a node
     that no run created, a rename, or a row made after the run in a table
     that cascades with a node. Those tables are views, reports, custom
     fields, statuses, types, tags, recurrences, watchers, grants and
     activity. `live_ws41_discard.py` reads the catalog and fails when a new
     table cascades with a node.
  4. The run was written before I-6 and has no `progress.created_nodes`.

  All four checks and the deletes run in one transaction, under the same
  advisory lock as the apply (§7.4). The transaction also locks the run's
  tasks and nodes `FOR UPDATE` before it checks. So neither an import nor a
  member's write can land between the check and the delete.

  **Rows a related import wrote are not a member's.** A later import can
  continue this run or target one of its nodes. It then adds statuses, task
  types and tags to those nodes. A later import that updated a task syncs its
  watchers. Its own discard leaves these rows.

  So those three tables, and task watchers, skip a row that such a RELATED
  run wrote. "Wrote" means between its first apply (`progress.started_at`,
  stamped by `START_SQL`) and its end. Without this, discarding the later run
  first would still leave this run refused for ever. `live_ws41_discard.py`
  check 4.6 fails without it.

  No other table gets the exception, and no unrelated run does. An upload
  left open hides nothing (check 3.5). The one gap is a member's status, type,
  tag or watch made while a related import was writing, which is minutes.
  A table that cascades with a task and is in neither list of
  `import_discard.py` fails check 0.2.

### 6.10 Side effects that stay off

An import writes hundreds of tasks at once. Everything that reacts to a task
write must not fire for it:

- No `pm_notifications` rows and no mail.
- No `emit("pm.task.created")`, so no workflow and no automation runs.
- No watcher rows except the mapped assignees, through `ensure_watchers`.
- One `system` activity per space when it is created, and one when the run
  ends, not one per task. Each carries `meta.import.run_id`.

**No flag was necessary (built in I-3).** The routes call `emit` and `notify`
AFTER the shared helpers return. The helpers themselves emit nothing. So the
writer calls the helpers and nothing else. `test_import_writer.py` fails if the
writer module ever calls `emit` or `notify`, and the live test counts zero
notification rows after a full import.

## 7. The run — storage, jobs and limits

### 7.1 `pm_import_runs` (next free migration number at build time, R1)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID NOT NULL | Tenant-scoped. RLS through the generated migration (R5(a)) |
| `created_by` | TEXT | The admin's email |
| `source` | TEXT | `clickup`, later `asana` and the others |
| `state` | TEXT CHECK | `uploaded`, `planned`, `applying`, `done`, `failed`, `discarded` |
| `files` | JSONB | Name, size, SHA-256 and stored path of each upload |
| `mapping` | JSONB | People, statuses, target, field types — what the admin confirmed |
| `plan` | JSONB | The dry-run counts, warnings and losses |
| `progress` | JSONB | Tasks written, the last batch, `node_map` of source ref to node id |
| `report` | JSONB | The final counts |
| `heartbeat_at` | TIMESTAMPTZ | The writer bumps it each batch |
| `created_at`, `updated_at`, `finished_at` | TIMESTAMPTZ | `updated_at` moves on each mapping save |

R6 expand only: a new table and a new index. Nothing existing changes.

### 7.2 Files

The upload goes to local disk, beside attachments, under a new
`PROJECT_IMPORT_DIR` (default `data/project_imports/<org_id>/<run_id>/`). The
writer re-parses the file at apply, so the stored plan never has to hold every
task. The files are deleted when the run reaches `done`, `failed` or
`discarded`, and a nightly sweep deletes any file older than 14 days.

**Built in I-2:** the delete on `discarded`. A new upload discards the
organization's open run (§7.4), so the disk holds one open upload per
organization at most, which is 250 MB. **Owed by I-3:** the delete on `done`
and `failed`.

**Built in I-7:** the sweep (`import_sweep.py`), which the
gateway starts at boot and runs twice a day. It deletes a run folder untouched
for 14 days, and an organization folder left empty. So it also removes the
folder of an organization that no longer exists, because the row CASCADEs and
the folder does not. It reads the disk only. `test_import_sweep.py` fences it.

### 7.3 The apply job

There is no durable job runner (the research found APScheduler, `BackgroundTasks`
and an in-memory `JobTracker` only). The apply therefore works like this:

1. `POST …/apply` takes a transaction lock per organization
   (`pg_advisory_xact_lock`). It refuses (409) while another run of the
   organization is `applying` with a fresh heartbeat. Then it sets
   `state = 'applying'`, writes a new LEASE id into `progress.lease`, and
   starts an `asyncio` task that holds that lease.
2. The task binds `tenant_session(run.organization_id)` itself, per batch.
   It never trusts a tenant from the request (R5(e)).
3. The task re-checks the plan against the database as it is NOW. A member
   who left since the dry run fails the run with a named reason.
4. Batch 0 writes every node and each project's status set, and stores the
   node map in `progress`, in ONE transaction. It reuses earlier nodes (§6.9)
   and checks a `group:` grant again.
5. Before each later batch, a short transaction reads which tasks exist. It
   then reserves task numbers for the rest (`core.reserve_task_numbers`).
   So the counter row of a live space is locked for milliseconds, not for a
   whole batch. A skipped task takes no number.
6. Each batch writes 200 tasks in one transaction, parents before children,
   and saves `progress.cursor`.
7. A ticker bumps `heartbeat_at` every 30 seconds, apart from the batches.
8. The end writes the report, one activity per space with that space's own
   count, sets `done`, and deletes the upload.

**One writer (built in I-3).** Every write the job makes names its lease:
the progress, the heartbeat, `done` and `failed`. A resume after a stale
heartbeat takes a NEW lease. A writer that outlived its heartbeat then loses
its next write and stops. It never marks the run failed, and it never deletes
the upload. The live test proves each step.

**Retry.** A deploy applies migrations while an import may run, and a
migration's lock can deadlock a batch. A batch that fails with a deadlock, a
serialization failure or a lock timeout (SQLSTATE 40P01, 40001, 55P03) runs
again, up to 4 times. The batch is one transaction that skips what exists, so
a retry is safe. The live test saw real deadlocks on the shared scratch
database, and the retry carried the run through each time.

**Resume.** If the gateway restarts mid-run, the heartbeat goes stale. After
2 minutes, `POST …/apply` resumes the run from `progress.cursor`.

**Failure.** Any other error sets `failed`, records the reason in `report`,
and deletes the upload. The reason is the refusal text, or the detail of a
refused helper, never only an exception name. The rows already written stay.
A new upload of the same file reuses the tree, skips those rows and writes
the rest.

### 7.4 Limits (phase 1)

| Limit | Value | Why |
|---|---|---|
| Upload size | 50 MB per file, 5 files per run, on the gateway. **9.5 MB per run in the browser** (I-4) | The largest reasonable Space export. The Next proxy in front of the gateway buffers 10 MB of a request body and cuts the rest (`experimental.proxyClientMaxBodySize`). So the wizard refuses a larger upload with a reason, and the admin exports one Space at a time. The real P-1 file is 1.2 MB. **The gateway also refuses a body the proxy cut** (I-7): the wizard sends `expected_files` and `expected_bytes` AFTER the files, and a cut body loses them. Measured through the real proxy: a 12 MB single file, and a 5 MB file followed by a 12 MB file, each got the "arrived incomplete" reason and wrote no run. A 9.7 MB file parsed and was refused on its task count in 5.6 s, inside the proxy's 30 s timeout |
| Tasks per run | 20,000 | Twice the Jira file cap. A larger workspace imports one Space per run |
| Runs in flight | 1 per organization | One writer at a time in one tree |
| Open runs | 1 per organization | A new upload discards the open run and deletes its files (built in I-2). This caps the disk an organization can hold |
| Who may run it | `admin:access:manage` | The same gate the CRM Zoho import uses (`crm/import_zoho.py`). §11 Q-2 |

### 7.5 Flags

Ship dark, default OFF:

- Gateway: `PROJECTS_IMPORT`, read at call time like `PROJECTS_ORG_VOCABULARIES`
  (`core.py:2096-2117`). OFF, every import route answers 404 to a member
  who can reach Projects. The router's own gate runs first. So a caller who
  is not signed in still gets 401. A member without `feature:projects` still
  gets 403, as on every other Projects route.
- Client: `NEXT_PUBLIC_PROJECTS_IMPORT`, as a literal `process.env` read.
  I-4 adds it with the wizard. No client code exists before I-4.
- Storage: `PROJECT_IMPORT_DIR` (§7.2). Both gateway values are in
  `.env.example`.

### 7.6 Routes

All under `/projects/import`, all behind the flag and the permission:

| Route | Does |
|---|---|
| `POST /projects/import/runs` | Uploads the files, detects the source, parses, returns the run and the bundle summary |
| `GET /projects/import/runs/{id}` | The run, its plan, its progress cursor and its report |
| `PUT /projects/import/runs/{id}/mapping` | Saves the mappings, re-plans, returns the plan |
| `POST /projects/import/runs/{id}/apply` | Starts or resumes the apply |
| `POST /projects/import/runs/{id}/discard` | §6.9 |
| `GET /projects/import/runs` | This organization's runs |

### 7.7 The surface

The import lives **inside Projects**. It adds no pane and no nav entry, so
the nine-pane allowlist does not change (`launch_surface.md` §2). The entry is
a labelled row, "Import from ClickUp", in the Projects sidebar above Spaces. An
organization with no spaces also gets a button under the empty tree (I-7).
⚠️ **Superseded.** WS-42 (D81) moved the import into Projects, Settings, Import
& export. The owner removed the empty-tree button on 2026-09-30, so Settings is
the only entry (`projectsSettings.test.ts`).

An icon beside the + was the only entry in I-4, and the owner read it as "no
import UI". The entry shows only to a member with `admin:access:manage`, only
when the flag is on, and only after access has loaded. The dialog mounts in the page's `overlays`, so it
opens on a phone too (the H-120 defect).

A five-step wizard, built from `src/components/ui/` primitives and the one
look (`DESIGN_SYSTEM.md`). I-8 added step 3 and moved the target into it:

1. **Upload** — the source picker, the file drop, and the export steps for
   the chosen tool, in plain words.
2. **Review** — the counts per space and project, the warnings, and the
   losses, before any write.
3. **Spaces** (I-8) — the target, who can see a new space, and the tree:
   untick a Space, Folder or List to leave it out, or rename it.
4. **Map** — the people, the statuses (stage and name, and two names merge),
   and the columns the importer does not read. Custom field types join in I-5.
5. **Import** — the progress, then the report with a link to the new space.

**The Statuses section of Map (I-10).** The rule is "show the result, and
hide the work". Most imports need no change, so the admin sees the outcome
first.

- **The summary.** One line, for example "Your 10 ClickUp statuses become 6
  statuses in Fracktal". Under it, one chip per target status, in stage
  order. The chip colour comes from `statusAccent.ts`, never from a new
  palette. A chip marks a status that the import adds as "new". Under the
  chips, one line per merge, for example "Closed, done and completed become
  Done". The button "Review mapping" opens the rows.
- **The rows.** One row per ClickUp status: its name, and its task and List
  counts. Each row has ONE control, a "Becomes" picker. It replaces the name
  box and the stage dropdown of I-8. The picker holds three groups:
  1. **In this space**: the statuses of the target set, each with its stage.
  2. **Added by this import**: the new statuses that other rows already make.
  3. **New status…**: this opens a name box and a stage picker in the row.
- **A row that points at an existing status** shows that status's stage, as
  fixed text. A row that makes a new status shows the stage picker.
- **The I-8 merge mark stays.** When two ClickUp statuses with different
  proposed stages share one target, the row says so.
- **No new primitive.** The picker is `SelectButton` with option groups and
  its filter. The rows sit in a `CollapsibleSection`. Both live in
  `src/components/ui/`.

**The Tags and types section of Map (I-11)** follows the same shape: a
summary, one button, and rows with one "Becomes" picker. Its groups are
"Shared in your organization", "In this space", "New shared tag" and
"Leave out".

**An earlier import of the same workspace (built in I-4).** The plan carries
`inherited_from` and `continues`. The upload and the mapping route both ask
`import_writer.continues_earlier`, which is the writer's own reuse rule, read
only. A second copy of the rule would drift from what the writer does. The review and
Map steps say either "goes into the same spaces" or "starts a new tree".

When a
saved mapping breaks a continuation, the first Import stops and shows why. The
second Import goes ahead. The report counts `lanes_added`, the statuses the
writer added to a list that was already in Metorite.

**A run the admin does not watch.** The view sends the writer's cursor and the
report, and never the lease or the node maps. The wizard polls with the dialog
closed too, so the tree refreshes when the run ends. A reopen shows a run that
still writes, or a report not yet seen. A failed poll backs off and goes on.
After 150 s with no progress, the wizard offers Resume, and the server refuses
it with 409 while the writer is alive.

## 8. Fences (R7)

| Rule | Fence |
|---|---|
| The importer opens no network connection | `tests/unit/test_import_no_network.py` — no module under `projects/importer/` imports `httpx`, `requests`, `aiohttp`, `urllib.request` or `socket` |
| One write path into `pm_tasks` | `tests/unit/test_pm_task_insert_sites.py` — no module writes a raw `INSERT INTO pm_tasks`, and exactly five modules create tasks through `insert_row`, the writer among them |
| No connector | `tests/unit/test_no_task_provider_connectors.py` stays green, unchanged |
| The adapter reads real files | `tests/unit/test_import_clickup_adapter.py` over `tests/unit/import_fixtures/clickup_workspace.csv` (P-1). The fixture sits beside its test, because `tests/fixtures/` holds only fixtures that two languages read |
| The fixture holds no real data | `test_the_fixture_holds_no_real_contact_data` in the same file. `scripts/import_scrub_clickup.py` refuses to copy a column it has no rule for |
| Every adapter decodes and reads CSV the same way | `tests/unit/test_import_text.py` |
| The bundle holds `None`, never a guess | `tests/unit/test_import_clickup_adapter.py` — `test_every_whole_field_the_file_lacks_is_a_loss` (no guessed `completed_at`) and `test_every_column_of_the_real_file_has_a_stated_fate` |
| The plan writes nothing | `tests/unit/test_import_plan.py` — the plan module imports no database client (`test_the_plan_module_cannot_reach_a_database`). `tests/unit/test_projects_import_routes.py` records every statement an upload sends, and finds no write but the run's own |
| Side effects stay off | `tests/unit/test_import_writer.py` (`test_the_writer_never_emits_or_notifies`) — the cause. `tests/live/live_ws41_writer.py` check 3.4 — the result, zero notification rows |
| A retry is only for a transient lock error | `tests/unit/test_import_writer.py` — 40P01, 40001 and 55P03 retry. A constraint error does not |
| Tenant scope | `tests/unit/test_tenant_coverage.py` covers `pm_import_runs` |
| The SQL works (R8) | `tests/live/live_ws41_import.py` — the run table, the reads and RLS. `tests/live/live_ws41_writer.py` — apply, the takeover by lease, re-run with no new node, an existing-space target, the old importer's skip. `tests/live/live_ws41_discard.py` — discard: exact removal, the tombstones, each refusal, and a later update run discarded before the earlier one |
| The flag is dark by default | `tests/unit/test_projects_import_routes.py` (`test_the_flag_off_answers_404`, and the gate order on every route). `publicFlags.test.ts` holds the client flag from I-4 |

## 9. Slices

Every slice is **AGENT-SAFE** unless it says otherwise. Each one is one PR.

| Slice | Delivers | Done when |
|---|---|---|
| **P-1** 🔴 OWNER | One real ClickUp workspace export — ✅ **received 2026-09-27** (§4.1.1). One "All columns" view export — still owed, for I-5 | The real file stays outside the repo. I-1 commits a scrubbed fixture, which a script derives from the real file: every name, email, text, URL and id is replaced, and every shape and every count in §4.1.1 is kept |
| **I-1** ✅ built 2026-09-27 | `ImportBundle`, the ClickUp workspace-CSV adapter, the encoding sniff, the scrub script, the no-network fence | The adapter parses P-1 into a bundle whose counts match the file. Every field in §4.1 lands, or has a `Loss` row. **Met:** the scrubbed fixture and the real file give the same summary, and each §4.1.1 count has a test |
| **I-2** ✅ built 2026-09-27 | Migration 219 for `pm_import_runs` and the origin index. Upload, list, get and mapping routes (`routes/projects/imports.py`). The plan (`importer/plan.py`). The D80 docstring edits (§2) | A dry run of P-1 returns counts, warnings and losses, and writes no `pm_*` row. `live_ws41_import.py` plan half passes. **Met:** the route test records every statement and finds no write but the run's own. The live test passes 21 of 21, with RLS checked under a role that does not bypass it |
| **I-3** ✅ built 2026-09-28 | The writer (`import_writer.py`), the layout (`importer/layout.py`), `POST …/apply`, batches, resume, retry, the report | P-1 applies into a new space. Counts in the report match the file. A second run skips all. A killed run resumes to the same counts. **Met:** `live_ws41_writer.py` passes 39 of 39 on a real Postgres. It stops the run after 3 batches, takes it over with a new lease, and finishes. It then finds 2,423 tasks, 1,270 subtasks, 5 spaces, 9 folders, 48 projects, 1,647 closed, 93 comments with their ClickUp dates and zero notifications. A second run skips all 2,423 and creates no node. A list moved since is created again. A different workspace with the same Space names gets new spaces. An existing-space import follows the grammar and skips the old importer's task. The I-2 review advice is met without `ON CONFLICT` (§6.9): a pre-check per batch, the index as the backstop, `legacy_refs` skipped, the group checked, the files deleted |
| **I-3b** ✅ built 2026-09-28 | Update mode (§6.9, §11 Q-5) | A new export of the same workspace updates the tasks by the three-way rule, and adds new comments once. **Met:** `live_ws41_writer.py` passes 56 of 56. An edited export updates a title and a priority, keeps a member's conflicting title, keeps a member's description, and adds one new comment. The same export again changes nothing, and moves no `updated_at`. A changed people mapping unassigns nobody. A member-renamed lane is followed. The creating run stays on every task |
| **I-4** ✅ built 2026-09-28 | The wizard (§7.7) | An admin imports P-1 end to end in the browser, in light mode, at compact density, and at phone width (the `visual-review` skill). **From the I-3 review:** the review step says when a run will CONTINUE an earlier import's spaces, and when a changed name or grant will start a new tree instead. The report names any status lane the writer added to a set it did not create. The server needs a read for the first: `continued_from` is known only at batch 0 today. **Met:** the visual-review rig walked all four steps against the scrubbed P-1 plan, in dark and light mode, at compact and comfortable density, under a violet accent, and at 1280, 1440, 1920 and 390 px, with no console error. The plan carries `continues` (`imports._continues`, which asks `import_writer.continues_earlier`), and `live_ws41_writer.py` passes 58 of 58, with `continues` and `lanes_added` checked both ways. The I-4 review found the view dropped `progress` and `report`, which a stubbed rig cannot see. `test_the_view_carries_progress_and_report_but_not_the_lease` fences it. **End to end, 2026-09-28:** a browser walk with no stubs drove a real gateway and a fresh Postgres. In light mode it imported the fixture: 2,423 tasks, 5 spaces, 93 comments, with live progress. At compact density the same file again showed the continuation note and found 2,423 unchanged. At 390 px a third run did the same. No console error. The walk found a re-run that reported a Done status added to 7 lists it only reused. `project_statuses` now returns the lists, and the writer counts only those it creates. `live_ws41_writer.py` check 4.2 fences it |
| **I-5** ⏸ deferred 2026-09-28 | The ClickUp view-export join (custom fields) | Custom field values from the view file land on the right tasks. A count gap between the files is a warning. **Deferred:** customer zero uses no custom fields (owner, 2026-09-28), so the workspace export already carries all its data. The review step now says the file lacks custom fields only as a condition. Build this when a customer who uses them asks |
| **I-6** ✅ built 2026-09-28 | Discard (§6.9) | Discard removes exactly the run's rows, and refuses after a member edit, a member's new work in an imported node, or a later import built on the run. A reused node and an earlier run's task survive it. The tombstones reach the delta feed. The wizard lists recent imports, so a run can be opened or discarded after a page reload. **Met:** `live_ws41_discard.py` passes 32 of 32 on a real Postgres. A discard removes the run's 2,423 tasks and 62 nodes, and writes a tombstone per task. A member's edit, comment, personal triage, node and saved view each refuse it and delete nothing. With the member work gone, the same run discards. A later update run refuses the earlier run's discard. Its own discard removes its comment, keeps its update, and leaves the earlier tasks, and then the earlier run discards cleanly. A browser walk with no stubs, against a real gateway and database, imported and discarded in light mode, found the run again from the list after a reload at compact density, showed the member-edit refusal by task name, and drew the list at 390 px, with no console error |
| **I-7** ✅ live 2026-09-28 | Flip `PROJECTS_IMPORT` and `NEXT_PUBLIC_PROJECTS_IMPORT` on production. The owner asked for it on 2026-09-29 ("there is no UI/UX currently deployed"), and the `enforcement-flip` grant runs to 2026-11-30 | The owner flips them (§3a allows it until the window ends — name the box). **Before the flip:** the nightly sweep of §7.2 exists, and the proxy in front of the gateway caps a request body near 260 MB. FastAPI spools the whole multipart body to disk before any dependency runs, so the route's own 50 MB cap acts only after the upload has landed. **Built:** the sweep (§7.2) and a labelled entry (§7.7). The browser path is app.metorite.com, then the Next proxy, then the gateway on loopback, so it never meets Caddy's api host. The Next proxy caps it at 10 MiB, and the trailing fields of §7.4 refuse a cut body. The gateway caps an import upload at 260 MB BEFORE it is read (`import_body_limit.py`, a pure-ASGI wrapper), because FastAPI spools a whole body ahead of every dependency, sign-in included. The cap is not in Caddy: a new matcher there changes the sign-in lines `test_caddy_auth_gate.py` holds for the owner, and the browser path never passes Caddy's api host. `test_import_body_limit.py` fences it. The flip follows the merge, and its evidence is recorded here. **Flipped on 2026-09-28, box srv1914284.** `PROJECTS_IMPORT=1` went into `/opt/acb/app/.env`, and `NEXT_PUBLIC_PROJECTS_IMPORT=1` into `workbench/control_plane/.env.local`, before the merge of #526. Backups of both files sit beside them as `*.bak-pre-import-flip`. **Evidence:** `/version` serves `b35399b8`. The gateway process restarted at 19:38:33 UTC with the flag in its environment, and it logged `projects.import.sweep_started`. The workbench rebuilt at 19:40:22 UTC. Its static bundle holds no unset `NEXT_PUBLIC_PROJECTS_IMPORT` literal, so the value was inlined, and it holds the entry text |
| **I-8** ✅ built 2026-09-30 | The mapping step, made complete (owner, 2026-09-30: "sort out the unknown fields"). A **Spaces** step between Review and Map shows every ClickUp Space, Folder and List. The admin unticks one to leave it out with everything under it, or renames it. The same step sets **who can see** a new space: the organization, or one group (§5.3). On **Map**, a status takes a new name, and two statuses given one name merge (§6.3). The columns the importer does not read are listed with their counts and examples, and each is left out or kept as a line in the task's description (§6.5b) | `plan.choose` is the ONE place the choices apply: the dry run and the writer both read through it (`test_import_choices.py`). A subtask of a skipped task stays out, wherever it lives. **Review fixes:** a later export that keeps OTHER lists still continues the earlier space, because "same export" is judged on every task in the file (`file_task_refs`). A choice for a container the file no longer holds is dropped, never an error. A rename in an existing space keeps the folder a re-run reuses (`source_name`). The Map step never changes a stage by itself, and marks a merge whose stages differ. **Met:** `live_ws41_choices.py` passes 14/14 on Postgres. A skipped list creates no node and no task, a renamed list lands under its new name, a kept column reaches the description, and a second run that keeps everything adds the rest into the same space. The writer, import and discard live suites still pass (58, 21, 33). A browser walk against a real gateway ran the five steps on a fixture with two unknown columns |
| **I-9** ✅ built 2026-09-30 | People added AFTER an import get their tasks (§6.2, §6.9). A production dry run of the real export (read only, counts only, 2026-09-30) found the organization's People directory holds 1 person, so 50 of 51 ClickUp people had no member and 2,024 assignments would land unassigned. The three-way rule moved nothing on a mapping change, so adding the team later and importing again did not help. Now the unchanged-source branch FILLS A GAP: when no member edited the field, a re-run adds newly matched people to the assignees and rewrites the description's "Assigned in ClickUp to" line (`layout._fills_a_gap`). It never takes anybody off (4c.3 still holds). The Map step and the report say how to close the gap | `tests/live/live_ws41_people_later.py` passes 10/10 on Postgres, through the upload route's own inheritance: an unassigned task is assigned on the next run once the person is in People, the name line goes, and a member's own assignment is kept. `live_ws41_writer.py` still passes 58/58. Unit: `test_import_layout.py` pins the fill, the member-edit guard and the add-only rule |
| **I-10** ✅ built 2026-10-09 | Statuses: one set per space, a target for each ClickUp status, and a Map section that opens on a summary (§6.3, §7.7). Owner decisions, 2026-10-09 | **Plan:** the plan carries the target set and the proposed target of each ClickUp status. On the fixture, the ten statuses propose six targets, and the plan holds no error. A choice that names an existing status takes that status's stage. **Writer:** `live_ws41_writer.py` gains checks. A new space holds one set, and every List that the run creates sets `owns_statuses = false`. Each task's status comes from the space's set, and a task with no status is in Backlog. An existing-space target gains only the missing names, through `_reuse_statuses`, and keeps them after a discard. A re-upload of a pre-I-10 tree adds 0 lanes. **Checks that change by design, with their new values:** writer 3.5, `done_status_added` goes from 7 to 0. Writer 4c.4 finds its lane as the space's "Backlog", on a node with no parent. Discard 4.5b matches the lane name case-blind. Every other check of the live writer, choices, discard and people-later suites passes unchanged. **Ships under the live flag.** `PROJECTS_IMPORT` is on in production (I-7), so the merge is the release. Production held no applied import on 2026-10-09, only one planned run and two discarded ones. **Surface:** `importFlow.test.ts` pins the summary text, the merge lines and the picker groups. The `visual-review` walk covers the summary and the open rows in light mode, at compact density, under a changed accent, and at 390 px. **Met:** `live_ws41_writer.py` passes 82 of 82 on a fresh private ladder database, with the fixes of the I-10 review (checks 4.2b, 8.1, 8.2 and 9.1 to 9.3). A mutation of each fix fails its check. A new space holds one set of six names, and no List or Folder that the run makes owns one. Each task takes a status of its space's set. A task with no status lands in Backlog. Review lands after In progress, and On hold after Backlog. The hand-made space keeps its "To do", gains the five names it lacks, and keeps them after a discard. A re-upload of a pre-I-10 tree, rebuilt by hand, adds 0 lanes and moves no task. Choices passes 14 of 14, discard 33 of 33, people-later 10 of 10 and import 21 of 21. Two mutations prove the continuation fences. Without `progress.status_names`, check 4.2 fails. A run that continues and still takes the proposals adds 39 lanes, and check 7.2 fails. `importFlow.test.ts` passes 65 of 65. The rig walked Upload, Review, Spaces and Map against the fixture's plan, with the API stubbed, and showed no console error. The chip paint did not move under two accents |
| **I-11** | Tags and task types land in the shared vocabulary, with the same summary-first section (§6.5, §7.7). Owner directive, 2026-10-09 | A ClickUp tag that matches an existing org-wide or space tag, case-blind, uses it. A new tag lands org-wide (`project_id IS NULL`). A type follows the same rule. Without `admin:settings:manage` or the flag, both land on the target space, and the step says so before the import. The caps count the effective set. R8: a live check finds the org-wide rows, and finds no tag row on any List that the run creates. The update rule's handling of a tag that moved from a List to the organization is stated in §6.5 and has a test |
| **I-12** ⏸ waits for a file | A column the importer does not read can become a shared custom field, with the §6.5 type guess. It is a third choice beside "Leave out" and "Keep in the description" (§6.5b) | Build this when a customer's file carries such columns. The Fracktal workspace export carries none, because ClickUp puts custom fields only in a per-view export (I-5) |
| **I-13** later | Jev proposes a target status, tag or type for a name that the I-10 and I-11 rules cannot place. The admin confirms each one (owner, 2026-10-09: "rules now, Jev later") | Every proposal shows as a proposal, and nothing applies without the admin's choice. The spend goes through the metered path, so H-171 does not grow |

**Then one adapter per slice, in this order.** Each needs its own real sample
file first, exactly like P-1:

| Order | Tool | File | Why this place |
|---|---|---|---|
| A-1 | Asana | Project CSV, then project JSON | Common, any member can export, emails may be present |
| A-2 | Jira Cloud | Issue search CSV (all fields) | Common. Comments present. The repeated header is the parser test |
| A-3 | Trello | Board JSON | Structured and complete except old comments |
| A-4 | Linear | Workspace CSV | Status type is recoverable from the timestamps |
| A-5 | Todoist | Project CSV | Simple. The `INDENT` tree and the inverted priority are the traps |
| A-6 | monday.com | Board XLSX | Needs an XLSX reader. No `pyproject.toml` names `openpyxl` on 2026-09-26, so this slice adds the dependency, and I-5 adds it first if a ClickUp view export arrives as XLSX |
| A-7 | MS Planner | XLSX | Small and flat |
| A-8 | Notion | Markdown & CSV ZIP | A ZIP needs a size and path check before extract |
| A-9 | Wrike | Backup ZIP of JSON | The best Wrike file, admin and paid plan only |
| A-10 | Smartsheet | XLSX | Status is only a user column |
| — | Basecamp | — | **Not planned.** HTML only. Revisit if a customer asks |

A ZIP adapter must check the total uncompressed size, the file count and every
path before it extracts anything (zip-bomb and path-traversal checks).

## 10. Verification commands

Each slice adds its files to this list. Run every line that exists at
the slice you verify.

**I-1 (built):**

```bash
uv run pytest tests/unit/test_import_clickup_adapter.py tests/unit/test_import_text.py \
  tests/unit/test_import_no_network.py tests/unit/test_no_task_provider_connectors.py
uv run ruff check apps/services/gateway/gateway/routes/projects/importer \
  scripts/import_scrub_clickup.py tests/unit/test_import_*.py
uv run mypy apps/services/gateway/gateway/routes/projects/importer
node .claude/hooks/ste-lint.mjs project-docs/specs/project_import.md
```

**I-2 (built):**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_import_plan.py tests/unit/test_projects_import_routes.py \
  tests/unit/test_projects_routes.py tests/unit/test_tenant_coverage.py \
  tests/unit/test_migration_prefixes.py tests/unit/test_projects_migration.py \
  tests/unit/test_projects_chat_coverage.py tests/unit/test_import_no_network.py
uv run python tests/live/live_ws41_import.py
uv run ruff check apps/services/gateway/gateway/routes/projects/imports.py
```

`test_projects_chat_coverage.py` fails when a Projects route has no row in
the assistant's manifest. Each new route needs one, even an excluded one.
`test_projects_import_routes.py` holds the flag tests that §8 names
`test_import_flag.py`. The live test is a script, as every file in
`tests/live/` is, so run it with `python` and read its PASS lines.

**I-3 (built):**

```bash
uv run pytest tests/unit/test_import_layout.py tests/unit/test_import_writer.py \
  tests/unit/test_pm_task_insert_sites.py tests/unit/test_projects_import_routes.py
uv run python tests/live/live_ws41_writer.py
```

`live_ws41_writer.py` COMMITS: the writer opens its own sessions. It works in
a fresh organization and deletes it at the end. The scratch database is shared
between worktrees, so a neighbour's migration can deadlock a batch. The retry
covers that, and a cleanup failure prints `WARN` instead of hiding the result.

**I-4:**

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
uv run pytest tests/unit/test_projects_import_routes.py -q
```

**I-6:**

```bash
uv run pytest tests/unit/test_projects_import_routes.py tests/unit/test_projects_routes.py tests/unit/test_projects_chat_coverage.py -q
uv run python tests/live/live_ws41_discard.py
```

`live_ws41_discard.py` COMMITS, as the writer's test does, in three fresh
organizations that it deletes at the end.

**I-10:**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_import_plan.py tests/unit/test_import_layout.py \
  tests/unit/test_import_writer.py tests/unit/test_import_choices.py \
  tests/unit/test_projects_import_routes.py -q
uv run python tests/live/live_ws41_writer.py
uv run python tests/live/live_ws41_choices.py
uv run python tests/live/live_ws41_discard.py
uv run python tests/live/live_ws41_people_later.py
uv run ruff check apps/services/gateway/gateway/routes/projects
uv run mypy apps/services/gateway/gateway/routes/projects/importer
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

Run the four live suites one at a time. They share the scratch database, and
two runs at once can deadlock each other.

Without the database, the live suite SKIPS and the run reads green. Check the
skip count (CLAUDE.md §6).

## 11. Open questions for the owner

Each has a recommended default. The build proceeds on the default unless the
owner says otherwise.

| # | Question | Recommended default |
|---|---|---|
| Q-1 | Attachments: names only, or a later slice that accepts the files in a ZIP? | Names only in phase 1. A ZIP upload later, inside the 10 MB per-task budget |
| Q-2 | Who may import? | Organization admins only (`admin:access:manage`) |
| Q-3 | A checklist becomes a Markdown list in the description, or subtasks? | A Markdown list. Subtasks would inflate the task count and the workload views |
| Q-4 | Unmatched people: unassigned with the name kept, or a directory-only `people` row? | Unassigned with the name kept. No person row appears without an admin act |
| Q-5 | A re-run skips existing tasks, or updates them? | ✅ **Answered 2026-09-28: UPDATE.** Built as I-3b by the three-way rule of §6.9, so an update never overwrites a member's edit |
| Q-6 | A closed task has no completion date in the file. What date does it get? | The latest date the task carries, marked as an estimate (§6.6). Never the import time |
| Q-7 | May the dry run read `pm_tasks.clickup_id` to find work the old importer already wrote? | Yes, read only, for the dry run and the skip. Nothing writes the column |

## 12. Gate labels

- **AGENT-SAFE:** I-1 to I-6, and A-1 to A-10.
- **OWNER-GATE:** P-1 and every later sample file (real data, and a real
  ClickUp account), and I-7 (a production flag that makes a customer-visible
  feature live).
- **Never, under any grant:**
  - a live connection to a source tool,
  - an invite or mail to an imported person,
  - a write into a live organization's membership.
