# Project import — bring work in from another tool by file

**Status: ACTIVE — I-1 and I-2 built 2026-09-27, the rest is spec.** Owner
directive, 2026-09-26.
Verified against code on 2026-09-26 at `main` `04995db9`. One real ClickUp
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
- **Sync or re-sync.** A second run of the same file skips what exists (§6.9).
  It never updates a task that the first run wrote.
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
   stays a date. It lands in `start_date`, or at the end of that day in
   `due_at`.
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
  it.

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
`group:<slug>`. The importer never writes an email grant. It never proposes a
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
- `created_at` keeps the source date. `created_by` is the mapped member, or
  `system:import:<source>` when the author has no member.

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

### 6.3 Statuses

Metorite keeps a status set on the nearest node with `owns_statuses`
(migration 196). ClickUp keeps one per List.

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

- **Tags** go through `apply_task_tags`, scoped to the imported project.
- **Task Type** (ClickUp) maps by name to a `pm_task_types` row of the root,
  and creates the type when it is missing.
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

### 6.6 Dates, estimates and time

- `due_at`, `start_date` and `created_at` map as named, from the epoch
  column. A due time of exactly 04:00 in the file's zone is a date with no
  time (§4.1.1 fact 9).
- **`completed_at` has no source in the ClickUp file.** 1,647 of 2,423 real
  tasks are closed. The rule, pending §11 Q-6: take the latest date the task
  carries — a past due date, the last comment, or the creation date. Set
  `origin.completed_at_estimated = true`. **Never use the import time.** It
  would put 1,647 completions into one day of every report and chart.
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
- **Re-run.** A task whose `(source, external_id)` exists in this organization
  is **skipped**, not updated. The report counts the skips. This keeps every
  edit made in Metorite since the first run.
- **Discard.** The run page offers "Discard this import" for 14 days. It
  deletes the space the run created, which cascades. For a run into an
  existing space, it deletes the rows that carry this `run_id` and the nodes
  the run created. It refuses when any imported task has gained a comment or
  a status change from a member since the run, and it names those tasks.

### 6.10 Side effects that stay off

An import writes hundreds of tasks at once. Everything that reacts to a task
write must not fire for it:

- No `pm_notifications` rows and no mail.
- No `emit("pm.task.created")`, so no workflow and no automation runs.
- No watcher rows except the mapped assignees, through `ensure_watchers`.
- One `pm_activities` row of kind `import` per project, not one per task, as
  the old importer did with `sync` activities.

The writer passes one `quiet=True` argument to the helpers that emit. A fence
test proves the flag suppresses each one (§8).

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
and `failed`. **Owed before I-7:** the nightly sweep. It also removes the
folder of an organization that no longer exists, because the row CASCADEs
and the folder does not.

### 7.3 The apply job

There is no durable job runner (the research found APScheduler, `BackgroundTasks`
and an in-memory `JobTracker` only). The apply therefore works like this:

1. The confirm route sets `state = 'applying'` and starts an `asyncio` task.
2. The task binds `tenant_session(run.organization_id)` itself, per batch.
   It never trusts a tenant from the request (R5(e)).
3. Batch 0 writes every node, and stores `node_map` in `progress`.
4. Later batches write 200 tasks each, in one transaction per batch, parents
   before children. Each batch bumps `heartbeat_at`.
5. The last batch writes the links and the per-project activity row.

If the gateway restarts mid-run, the heartbeat goes stale. After 2 minutes
the run page shows "Interrupted" and offers **Resume**. A resume is safe,
because §6.1's index skips every task already written.

### 7.4 Limits (phase 1)

| Limit | Value | Why |
|---|---|---|
| Upload size | 50 MB per file, 5 files per run | The largest reasonable Space export |
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
| `GET /projects/import/runs/{id}` | The run, its plan, its progress and its report |
| `PUT /projects/import/runs/{id}/mapping` | Saves the mappings, re-plans, returns the plan |
| `POST /projects/import/runs/{id}/apply` | Starts or resumes the apply |
| `POST /projects/import/runs/{id}/discard` | §6.9 |
| `GET /projects/import/runs` | This organization's runs |

### 7.7 The surface

The import lives **inside Projects**. It adds no pane and no nav entry, so
the nine-pane allowlist does not change (`launch_surface.md` §2). The entry is
"Import…" on the Projects sidebar menu, shown to an admin when the flag is on.

A four-step wizard, built from `src/components/ui/` primitives and the one
look (`DESIGN_SYSTEM.md`):

1. **Upload** — the source picker, the file drop, and the export steps for
   the chosen tool, in plain words.
2. **Review** — the counts per space and project, the warnings, and the
   losses, before any write.
3. **Map** — people, statuses, custom field types and the target.
4. **Import** — the progress, then the report with a link to the new space.

## 8. Fences (R7)

| Rule | Fence |
|---|---|
| The importer opens no network connection | `tests/unit/test_import_no_network.py` — no module under `projects/importer/` imports `httpx`, `requests`, `aiohttp`, `urllib.request` or `socket` |
| One write path into `pm_tasks` | `tests/unit/test_pm_task_insert_sites.py` — the literal `INSERT INTO pm_tasks` appears only in the allow-listed helper files |
| No connector | `tests/unit/test_no_task_provider_connectors.py` stays green, unchanged |
| The adapter reads real files | `tests/unit/test_import_clickup_adapter.py` over `tests/unit/import_fixtures/clickup_workspace.csv` (P-1). The fixture sits beside its test, because `tests/fixtures/` holds only fixtures that two languages read |
| The fixture holds no real data | `test_the_fixture_holds_no_real_contact_data` in the same file. `scripts/import_scrub_clickup.py` refuses to copy a column it has no rule for |
| Every adapter decodes and reads CSV the same way | `tests/unit/test_import_text.py` |
| The bundle holds `None`, never a guess | `tests/unit/test_import_clickup_adapter.py` — `test_every_whole_field_the_file_lacks_is_a_loss` (no guessed `completed_at`) and `test_every_column_of_the_real_file_has_a_stated_fate` |
| The plan writes nothing | `tests/unit/test_import_plan.py` — the plan module imports no database client (`test_the_plan_module_cannot_reach_a_database`). `tests/unit/test_projects_import_routes.py` records every statement an upload sends, and finds no write but the run's own |
| Side effects stay off | `tests/unit/test_import_quiet.py` — no notification, no emit, one activity per project |
| Tenant scope | `tests/unit/test_tenant_coverage.py` covers `pm_import_runs` |
| The SQL works (R8) | `tests/live/live_ws41_import.py` — a real Postgres: apply, re-run skips, resume after a kill, discard |
| The flag is dark by default | `tests/unit/test_projects_import_routes.py` (`test_the_flag_off_answers_404`, and the gate order on every route). `publicFlags.test.ts` holds the client flag from I-4 |

## 9. Slices

Every slice is **AGENT-SAFE** unless it says otherwise. Each one is one PR.

| Slice | Delivers | Done when |
|---|---|---|
| **P-1** 🔴 OWNER | One real ClickUp workspace export — ✅ **received 2026-09-27** (§4.1.1). One "All columns" view export — still owed, for I-5 | The real file stays outside the repo. I-1 commits a scrubbed fixture, which a script derives from the real file: every name, email, text, URL and id is replaced, and every shape and every count in §4.1.1 is kept |
| **I-1** ✅ built 2026-09-27 | `ImportBundle`, the ClickUp workspace-CSV adapter, the encoding sniff, the scrub script, the no-network fence | The adapter parses P-1 into a bundle whose counts match the file. Every field in §4.1 lands, or has a `Loss` row. **Met:** the scrubbed fixture and the real file give the same summary, and each §4.1.1 count has a test |
| **I-2** ✅ built 2026-09-27 | Migration 219 for `pm_import_runs` and the origin index. Upload, list, get and mapping routes (`routes/projects/imports.py`). The plan (`importer/plan.py`). The D80 docstring edits (§2) | A dry run of P-1 returns counts, warnings and losses, and writes no `pm_*` row. `live_ws41_import.py` plan half passes. **Met:** the route test records every statement and finds no write but the run's own. The live test passes 21 of 21, with RLS checked under a role that does not bypass it |
| **I-3** | The writer, the batches, resume, the quiet flag, the report | P-1 applies into a new space. Counts in the report match the file. A second run skips all. A killed run resumes to the same counts. **From the I-2 review:** name the conflict target in full — `ON CONFLICT (organization_id, (origin->>'source'), (origin->>'external_id')) WHERE origin->>'kind' = 'import' DO NOTHING`. A bare `DO NOTHING` also swallows a `task_number` clash and drops a task in silence. Never write an import row with a NULL `external_id`, because NULLs never conflict. Skip the plan's `legacy_refs` too, which the index does not cover. Check that `group:<slug>` names a group of this organization. Delete the files on `done` and `failed` |
| **I-4** | The wizard (§7.7) | An admin imports P-1 end to end in the browser, in light mode, at compact density, and at phone width (the `visual-review` skill) |
| **I-5** | The ClickUp view-export join (custom fields) | Custom field values from the view file land on the right tasks. A count gap between the files is a warning |
| **I-6** | Discard | Discard removes exactly the run's rows, and refuses after a member edit |
| **I-7** 🔴 OWNER | Flip `PROJECTS_IMPORT` and `NEXT_PUBLIC_PROJECTS_IMPORT` on production | The owner flips them (§3a allows it until the window ends — name the box). **Before the flip:** the nightly sweep of §7.2 exists, and the proxy in front of the gateway caps a request body near 260 MB. FastAPI spools the whole multipart body to disk before any dependency runs, so the route's own 50 MB cap acts only after the upload has landed |

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

**I-3 and later (planned — these files do not exist yet):**

```bash
uv run pytest tests/unit/test_import_quiet.py tests/unit/test_pm_task_insert_sites.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

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
| Q-5 | A re-run skips existing tasks, or updates them? | Skip. An update would overwrite edits made in Metorite |
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
