# Shared mailbox — one company address that several members work from

**Status: ACTIVE (2026-10-23). The owner answered §1 on 2026-10-23. SM-1 and SM-2 dispatch
first.** Verified against the code of `origin/main` at `f72fafb92` on 2026-10-23.

**Origin:** owner decision, 2026-10-23: "Spec a shared mailbox". The example is
`sales@fracktal.in`, which several members of one organization read and answer.

**Board row:** WS-65 in `work_plan.md` §2, minted 2026-10-23.

**Single owner (contract point 6).** This file owns the shared mailbox. `email_app_master_plan.md`
keeps the private mailbox, the multi-inbox design (§11) and the held chat (§21). That spec lists
"a shared mailbox" as a non-goal (§10, line 4771). When the owner accepts this spec, that line
links here and adds nothing.

**Risk tier: HIGH.** Tenancy, identity and egress. Each slice runs the full loop of
`next-ticket.md` with an Opus review (CLAUDE.md §4).

---

## 0. The answer, in six rules

1. **A shared mailbox belongs to the organization, not to a member.** One row in
   `email_accounts` holds the connection. A column says it is shared. The member who connected it
   is a record, not the owner.
2. **Access is a grant in the one grammar of D12.** A grant names `org`, `group:<slug>` or an
   email address, and a level: `read`, `send` or `manage`. The validator is
   `acb_auth.permissions.normalize_grant_subject`. There is no second grant system.
3. **Row level security does not change.** It binds the organization (D15). The member boundary
   is the owner predicate in the gateway, and that predicate learns to read the grants.
4. **The mailbox stays the boundary of the AI context (D-EM-18).** The rules, the voice profile,
   the learned patterns and the drafting memory of `sales@` belong to `sales@`. They never mix
   with the private mailbox of the member who drafts.
5. **Every send names the member who pressed Send.** The record is in the database, in the same
   transaction that queues the send. A rule that sends names the rule and its author.
6. **Shared mail never leaves with a member.** Offboarding removes the grants of that member.
   It never deletes the mailbox, its mail or the grants of the other members.

A member with no grant on a shared mailbox sees no change anywhere.

---

## 1. Owner questions — answered 2026-10-23

**The owner answered all nine on 2026-10-23, and each answer is the recommendation below.** The
slices in §10 use these answers as written. They are decisions now (D-SM-Q1 to D-SM-Q9). On
the same day the owner accepted D-SM-1 to D-SM-9 (§4.1). Do not open them again.

| Question | Owner answer |
|---|---|
| Q-SM-1 | (a): a manager signs in as `sales@` itself. (b) is the later slice SM-11. |
| Q-SM-2 | Only a holder of the new permission `email:shared:manage`. |
| Q-SM-3 | One shared read state. |
| Q-SM-4 | Each member with `send` sees each draft. Only its author or a `manage` member edits or sends it. |
| Q-SM-5 | A shared mailbox counts against no member's 5. The organization holds at most 10 shared mailboxes. |
| Q-SM-6 | The mailbox signature, then the sign-off line of the member who sends. |
| Q-SM-7 | After a shared copy exists, the gateway refuses a new private connect of that address in that organization. |
| Q-SM-8 | No charge. |
| Q-SM-9 | Nobody by default. An unclaimed mail shows as "Unassigned" until a member claims it. |

The text of each question stays below, as the record of the choice.

1. **Q-SM-1 — How does the organization connect `sales@`?**
   (a) A manager signs in AS `sales@` itself, with its own password. The token belongs to the
   address. (b) A member signs in as themself. Microsoft gives that member delegated access to
   an Exchange shared mailbox (`Mail.ReadWrite.Shared`, `Mail.Send.Shared`). The token
   belongs to the member. (c) An app-only grant that the customer's admin consents to for the
   whole tenant.
   **Recommendation: (a) now, (b) as a later slice for Outlook only, (c) never for now.**
   (a) needs no provider change and works for Gmail and Outlook. A departure does not break it.
   (b) breaks when the member leaves, and Gmail has no API for it. (c) asks the customer for
   read access to every mailbox of their company, which is too much for one address.
2. **Q-SM-2 — Who may make a mailbox shared?**
   **Recommendation: a member who holds a new permission `email:shared:manage`.** The migration
   seeds it for the `admin` role of each organization, as migration 272 seeds
   `knowledge:manage`. A shared mailbox shows mail to other people, so it is an admin act.
3. **Q-SM-3 — Is the read state shared or per member?**
   **Recommendation: shared.** The read state is the flag of the provider, and Outlook and Gmail
   keep one flag for each mail. A per-member overlay disagrees with Outlook on the web. It also
   needs a table that grows with each member and each mail. "Who is answering this" (§5.2)
   carries the per-member signal instead.
4. **Q-SM-4 — Who sees a draft in a shared mailbox?**
   **Recommendation: each member with `send` sees each draft, and only its author or a
   `manage` member edits or sends it.** The provider keeps a draft in the Drafts folder of the
   mailbox. Outlook shows it to everyone who opens that mailbox. A private draft is a
   promise that the provider cannot keep.
5. **Q-SM-5 — Does a shared mailbox count against the cap of 5 mailboxes for each member?**
   **Recommendation: no, it counts against neither the member who connected it nor each member
   with a grant.** It counts against a separate cap for each organization, proposed at 10 shared
   mailboxes. D-EM-14 (500 MB) applies to the shared mailbox once. If a shared box counted for
   each member, the 6th grant would fail for a member who never connected anything.
6. **Q-SM-6 — What do recipients see as the signature?**
   **Recommendation: the signature of the mailbox, then one sign-off line for each member.** For
   example "Priya · Sales, Fracktal". A `manage` member sets the signature. Each member sets
   their own sign-off line for that mailbox. The sign-off line is off until the member writes
   one.
7. **Q-SM-7 — Can a private copy and a shared copy of one address live in one organization?**
   D-EM-17 lets two members keep two private copies of one address.
   **Recommendation: after a shared copy exists, the gateway refuses a new private connect of
   that address in that organization.** The refusal says "sales@fracktal.in is a shared
   mailbox. Ask a manager for access." A private copy that exists stays. Its owner sees a banner that offers to
   disconnect it. Two copies of one mailbox run two rule sets on the same mail, and both can
   answer one customer.
8. **Q-SM-8 — Is there a charge for a shared mailbox?**
   **Recommendation: no.** Pricing is flat (`launch_surface.md` §4), and a mailbox is not a
   seat. The AI work of the mailbox spends the organization's credits, as each private mailbox
   does today. This is a commercial call, so the owner decides.
9. **Q-SM-9 — Who answers mail that nobody claims?**
   **Recommendation: nobody by default.** An unclaimed mail that needs a reply shows in the
   Reply Zero of each member with `send`. It has the mark "Unassigned". A `manage` member can set
   one default assignee for the mailbox. Round-robin assignment is a non-goal (§2).

---

## 2. Scope and non-goals

**In scope.**

- A mailbox row that the organization owns, with a member on record as the connector.
- Grants of `read`, `send` and `manage` in the D12 grammar, and the owner predicate that reads
  them.
- Per-member state on shared mail: who answers a thread, and a collision guard at Send.
- The AI context, the voice, the rules and the held-chat classes of a shared mailbox.
- The send record, offboarding, All inboxes, the chat scope picker and the From picker.

**Non-goals.**

- **Send on behalf** ("Priya on behalf of Sales"). It needs Q-SM-1 (b), because a token of
  `sales@` cannot send as another user. Send as `sales@` is the only send mode until then.
- **A per-member read state** (Q-SM-3).
- **Round-robin or load-balanced assignment** (Q-SM-9).
- **A shared mailbox across two organizations.** One row is in one organization (D15). Two
  organizations that connect one address get two rows.
- **An app-only Microsoft grant** (Q-SM-1 (c)).
- **A Google Group as a collaborative inbox.** Google has no API for it.
- **A change to D-EM-17 for private mailboxes.** A private mailbox stays private. This spec
  adds a second kind of mailbox. It does not change the first kind.

---

## 3. Measured state (2026-10-23)

**The member boundary is in the gateway, not in the database.** The RLS policy on
`email_accounts` and on each email table is tenant-only:

```sql
CREATE POLICY email_accounts_tenant_isolation ON email_accounts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
```

(`infra/postgres/generated/04_policies.sql:403-405`, and `:501` for `email_messages`.) Inside
one organization, any SQL that drops the owner predicate reads the mail of every member.

**The owner predicate is `user_id = :uid`.** A count on 2026-10-23 found 87 sites in 26 files
under `apps/services/gateway/gateway/routes/email/`. Three helpers carry most of them:

| Helper | File | What it does |
|---|---|---|
| `_owned_accounts_sql` | `core.py:669` | The ids of the mailboxes of `:uid`, as a subquery |
| `_account_scope` | `core.py:688` | `em.account_id IN (<that subquery>)` |
| `_assert_account_owner` | `core.py:870` | 404 when the mailbox is not the member's |

Other sites write the predicate inline. Examples:

- `core.py:192`, the provider for a mail, and `core.py:214`, the provider for a mailbox
- `account_ref.py:71` and `teardown.py:139`
- the `sent` and `own` blocks of `automation/held_card.py:152-166`
- `KNOWN_DOMAINS_SQL` at `held_card.py:176-195`

**"Self" is the set of mailboxes with the same `user_id`.** `automation/identity.py:89-118`
joins `email_accounts o ON o.user_id = a.user_id`. A shared mailbox that kept the connector's
`user_id` would make `sales@` an address of the connector. Then mail from the connector to
`sales@` would count as mail to self (D-EM-27).

**The table has no owner kind.** `email_accounts.user_id` is `TEXT NOT NULL`
(`infra/postgres/17_email_accounts.sql:16`). Migration 223 made the unique rule per tenant.

**The agent sees only what `GET /email/accounts` lists.** `_accounts` and `_one_mailbox`
(`apps/agents/agent-email-assistant/agents.py:388`, `:460`) build each mailbox choice from that
list. So a shared mailbox reaches the chat when that route lists it, and not before.

**Drafting memory is keyed by member and mailbox.** `email_memory_scope` returns
`<member>#acct:<id>` (`core.py:1145-1176`). Two members who draft in `sales@` would write two
memories for one mailbox.

**A grant table with levels already exists.** `kb_space_grants` (migration 272) holds
`(organization_id, space_id, subject, level)` with `level IN ('read','write','manage')` and a
CHECK that mirrors the D12 grammar. `routes/knowledge/core.py:186-201` matches a grant against
`org`, the member's email and the member's groups, through `projects/core._MY_GROUPS_SQL`.
This spec copies that shape. It does not invent one.

**Offboarding purges each mailbox of the member.** `admin/members.py` reads
`read_teardown_plans(db, MemberScope(addr))` and records one purge for each mailbox, before the
delete. A shared mailbox must leave that scope.

**The 5-mailbox cap is an owner setting of 2026-10-23.** No constant in the code holds it on
`f72fafb92`. The slice that builds the cap must leave shared mailboxes out of its count
(Q-SM-5).

---

## 4. The model

### 4.1 Decisions (proposed)

| Id | Decision |
|---|---|
| **D-SM-1** | **A shared mailbox is a row of `email_accounts` with `shared_since` set.** `user_id` keeps the member who connected it, as a record. Every read that asks "whose is this mailbox" reads `shared_since` first. |
| **D-SM-2** | **Access to a shared mailbox is a row of `email_mailbox_grants`.** The subject is in the D12 grammar. The level is `read`, `send` or `manage`, and each level includes the one before it. |
| **D-SM-3** | **The connector gets no access from the connect.** The connect writes one `manage` grant for the connector, so the access is a grant like any other, and an admin can remove it. |
| **D-SM-4** | **A grant reaches only a member who has `feature:email`.** The gateway checks the feature through the one entitlement seam (`require_feature_router("email")`, `core.py:66`) before it reads a grant. A grant never gives the Email app to a member who lacks it. |
| **D-SM-5** | **A shared mailbox is never part of "self" for any member.** D-EM-27 self stays the private mailboxes of the member. Mail from Priya's private box to `sales@` is mail between two parties. |
| **D-SM-6** | **The shared mailbox is the AI context (D-EM-18 holds).** Rules, voice profile, learned patterns, sender lists, knowledge and drafting memory are keyed by the mailbox. The Mem0 scope is `shared#acct:<id>`, with no member in it. |
| **D-SM-7** | **Every send from a shared mailbox writes one row of `email_send_log`** with the member who pressed Send, in the transaction that queues the send. |
| **D-SM-8** | **Offboarding a member removes their email-address grants, and nothing else, from shared mailboxes.** A `group:` grant needs no change, because group membership leaves with the member. |
| **D-SM-9** | **A shared mailbox has at least one `manage` grant.** The gateway refuses a grant change or an offboarding that leaves none, and names the admins who can take it. |

### 4.2 Who owns the connection, and what happens when that member leaves

Under Q-SM-1 (a), a manager signs in as `sales@`. The token belongs to `sales@`. The connector's
departure does not touch the token. Sync goes on.

1. The offboarding read lists each shared mailbox where the leaver holds a grant. It also
   lists each one where `user_id` is the leaver.
2. The purge leaves out each mailbox with `shared_since` set. `read_teardown_plans` gets a
   predicate `shared_since IS NULL` on its member scope.
3. The offboarding removes each grant whose subject is the leaver's email.
4. If that removal leaves a mailbox with no `manage` grant, the offboarding stops with 409 and
   names the mailbox (D-SM-9). The admin adds a manager first.
5. `user_id` stays as the record of who connected it. The UI writes "Connected by Priya (left)".

Under Q-SM-1 (b), the token belongs to the member. When Microsoft disables that member, the
refresh fails, and the mailbox shows the reconnect banner of today to each `manage` member. A
`manage` member reconnects with their own delegated access. The reconnect path of D-EM-26
matches by address, and it keeps the mail, the rules and the grants.

### 4.3 Roles

| Level | May | May not |
|---|---|---|
| `read` | List, search and open the mail. Use the chat in the scope of the mailbox to ask and to search. See who answers each thread. | Send, draft, archive, move, label, claim a thread, change a rule |
| `send` | All of `read`. Reply, forward, send new mail as `sales@`. Draft. Claim and hand over a thread. Archive, move, label, snooze. | Change a rule, a setting, the signature or a grant. Disconnect. |
| `manage` | All of `send`. Rules, AI settings, the voice profile, the signature, the default assignee, grants, Process past emails, disconnect. | Delete the last `manage` grant (D-SM-9) |

**Why archive needs `send`.** An archive changes the mailbox for everybody. A reader who
archives hides a customer mail from the team.

**Send as, never send on behalf, in this spec.** With a token of `sales@`, the provider sends as
`sales@`. The customer sees `sales@` only. The record of the member is in Metorite (§7). Send on
behalf waits on Q-SM-1 (b).

### 4.4 The grant, in the one grammar

```sql
CREATE TABLE IF NOT EXISTS email_mailbox_grants (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    subject          TEXT NOT NULL
                     CHECK (subject = lower(subject)
                            AND (subject = 'org'
                                 OR subject ~ '^group:.+$'
                                 OR subject ~ '^[^@[:space:]]+@[^@[:space:]]+$')),
    level            TEXT NOT NULL CHECK (level IN ('read', 'send', 'manage')),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, account_id, subject)
);
```

It is the shape of `kb_space_grants`, with ENABLE and FORCE row level security and one policy
with USING and WITH CHECK. A trigger refuses a grant on a row whose `shared_since IS NULL`, so a
grant can never open a private mailbox. The route validates each subject with
`normalize_grant_subject`, and the CHECK only refuses a shape that no validator passes.

---

## 5. Per-member state on shared mail

### 5.1 Read state

Shared (Q-SM-3). The `is_read` of `email_messages` stays the provider's flag. A member who opens
a mail marks it read for the team, as Outlook does.

### 5.2 Who answers this — Reply Zero on a shared mailbox

One new table, `email_thread_assignments (organization_id, account_id, thread_id,
assignee_email, assigned_by, assigned_at)`, with the primary key `(organization_id, account_id,
thread_id)`. It is tenant-scoped with FORCE RLS. It holds one assignee for each thread of a
shared mailbox. A private mailbox never gets a row.

- **Claim.** A `send` member clicks "I'll answer". The row names them.
- **Auto-claim.** A send on a thread with no assignee claims it for the sender.
- **Hand over.** A `send` member names another member who holds `send`. The server checks the
  grant of the new assignee.
- **Reply Zero.** A NEEDS_REPLY thread of a shared mailbox shows in the Reply Zero of its
  assignee. With no assignee, it shows to each `send` member with the mark "Unassigned"
  (Q-SM-9). It never shows twice for one member.
- **A grant removed.** When a member loses `send`, their assignments clear, and the threads go
  back to "Unassigned".

The thread status (`replyzero.py`) stays per mailbox. A reply from any member of the mailbox
closes NEEDS_REPLY for all, because the reply is in the Sent folder of `sales@`.

### 5.3 Drafts

Per Q-SM-4. The local draft row gets a nullable column `drafted_by`. The list of drafts shows the
author.

Only the author or a `manage` member edits, sends or deletes the draft. The server checks
`drafted_by` on each draft write. A draft that Metorite did not write, for example from
Outlook on the web, has `drafted_by` NULL. Only a `manage` member may send it.

### 5.4 Two members reply at once

1. **Presence, as a hint.** A member opens a reply on a thread. Then the reading pane of the
   other members shows "Priya is replying". It is a key in Redis through the tenant prefix
   wrapper (R5), with a 60-second life that the composer renews. It blocks nothing.
2. **A check at Send, as the guard.** The composer sends `based_on_message_id`, the newest mail
   of the thread when the member opened the reply. The server compares it with the newest mail
   of the thread now. If a newer mail exists, the send answers 409. The answer holds "Rahul
   replied 2 minutes ago" and the text of that reply. The member chooses "Send anyway" or "Discard".
3. **A scheduled send** (migration 290) runs the same check when it fires. If it fails, the job
   holds the send and tells its author.

---

## 6. The AI on a shared mailbox

### 6.1 Whose context applies when Priya drafts from `sales@`

| Item | Source | Why |
|---|---|---|
| Mail, threads, contacts, sender lists | `sales@` | D-EM-18: the mailbox is the context |
| Rules, learned patterns, knowledge | `sales@` | D-EM-24: per mailbox |
| Voice profile | `sales@` | The customer hears one voice from one address |
| Signature | `sales@`, then Priya's sign-off line (Q-SM-6) | The address is the team. The line names the person |
| Drafting memory (Mem0) | `shared#acct:<id>` | D-SM-6. Priya's memory of her private box never reaches `sales@` |
| Chat memory ("keep replies short") | Priya | §11.3: it holds no mail, so it stays per member |
| Calendar for "when are you free" | Priya | She is the one who meets the customer |
| Identity in the draft | `sales@` and its display name | MB-14: the drafter speaks as the mailbox |

**Learning.** A send by any member teaches the voice profile of `sales@`. The voice job reads the
Sent folder of `sales@`, as it reads each mailbox today.

### 6.2 Held chat and egress

A chat that read mail of `sales@` is a held chat (§21 of the master plan). The held card stays.
Two classes change.

- **`org`** stays the organization's members, plus the addresses of the mailboxes that the
  member may send from. For a `send` member of `sales@`, that is their private mailboxes and
  `sales@`.
- **`sent`** is the people that the sending mailbox itself wrote to. For a card that sends from
  `sales@`, it reads the Sent folder of `sales@`, with the From check of fix round 1 (P1-a). It
  does not read the private Sent of the member. For a card that sends from a private mailbox, it
  stays as it is.
- **`seen`** stays not known. A sender of outside mail is the attacker of §21.2.

The SQL of `held_card.py:152-166` and `KNOWN_DOMAINS_SQL` change from `ea.user_id = :uid` to the
id of the sending mailbox. A test seeds a customer that only `sales@` wrote to and asserts that a
card from Priya's private box shows that customer unticked.

### 6.3 Rules on a shared mailbox

- **Who may create one.** A `manage` member only.
- **Whose name runs it.** The mailbox runs it, as a background act (§11.3). Each act that a rule
  records names `rule:<id>`, and `email_rules.created_by` names its author.
- **A rule that sends** (auto-reply, forward) writes `email_send_log` with `sent_by = 'rule:<id>'`
  and the author in `payload`.
- **A rule copy** (D-EM-24) between a private and a shared mailbox needs `manage` on the target.
  D-EM-29 applies, and the own-address set for a shared target is the address of the shared
  mailbox only.

---

## 7. Audit

One new table, `email_send_log (organization_id, id, account_id, sent_by, channel, draft_id,
provider_message_id, recipients_count, created_at)`. It is tenant-scoped with FORCE RLS and
append-only. The app role gets INSERT and SELECT, UPDATE on one column only (item 2), and no
DELETE.

1. Each send from a shared mailbox inserts one row before the provider call. The insert is in
   the transaction that records the send. The column `channel` is `web`, `chat`, `whatsapp`, `rule` or `scheduled`.
2. After the provider answers, the gateway sets `provider_message_id` on that row. The app
   role holds a column grant of UPDATE on that one column. A trigger refuses the UPDATE when the
   column is already set, and refuses each change to any other column.
3. Each grant change and each claim writes one `acb_audit` event, as `routes/knowledge/core.py`
   `audit()` does. These are admin acts, and a down audit backend never undoes them.
4. The reading pane of a Sent mail of `sales@` shows "Sent by Priya, from the chat".

**Why a table and not only `acb_audit`.** `acb_audit` drops an event when its backend is down.
The question "who sent this to the customer" must have an answer for each send.

---

## 8. Tenancy and RLS

### 8.1 The policy change

**No tenant policy changes.** Each new table gets the standard policy of
`generated/04_policies.sql`. The member axis stays in the gateway, as it is for every email table
today.

**The owner predicate becomes one seam.** Three SQL fragments in a new module
`routes/email/mailbox_access.py`:

| Fragment | Meaning |
|---|---|
| `REACHABLE_MAILBOXES_SQL` | Private mailboxes of `:uid`, plus shared mailboxes with a grant of any level that matches `:uid`, `org` or a group of `:uid` |
| `SENDABLE_MAILBOXES_SQL` | The same, with level `send` or `manage` on the shared arm |
| `MANAGED_MAILBOXES_SQL` | The same, with level `manage` on the shared arm |

`_owned_accounts_sql`, `_account_scope` and `_assert_account_owner` call these, with a level
argument. Each inline site of `user_id = :uid` moves to the helper, or gets a comment that says
why it is private-only. Identity (`identity.py`) stays private-only (D-SM-5) and says so.

Each fragment carries `AND organization_id = <bound tenant>` as defence in depth (R5), as
`_assert_account_owner(this_tenant=True)` does.

### 8.2 Expand and contract (R6)

1. **Expand, release N.** Next free migration number at build time (R1). Add
   `email_accounts.shared_since TIMESTAMPTZ NULL`. Create `email_mailbox_grants`,
   `email_thread_assignments` and `email_send_log`. Add `drafted_by TEXT NULL` on the draft row.
   Seed `email:shared:manage` for the `admin` role, and in `provision_org_roles`. All nullable or
   new. Old code reads `user_id = :uid`, so a shared row looks private to its connector and
   to nobody else. That is safe.
2. **Code, release N.** The seam of §8.1 ships behind `EMAIL_SHARED_MAILBOX`, default OFF. With
   the flag off, `REACHABLE_MAILBOXES_SQL` is the private arm only, and a test asserts it.
3. **Contract, a later release.** None planned. `user_id` stays NOT NULL as the connector.

### 8.3 The R8 fences

Each test runs against a real Postgres (R8), as the role `acb_app` that RLS binds, and carries
`pytestmark = _DB_GATE` so the deploy job skips it. Proposed file:
`tests/unit/test_email_shared_mailbox_r8.py`.

1. **No cross-org path.** Org A has `sales@` shared with `org`. A member of org B has the
   email address of a member of A. That member reads nothing of it. The test tries the list,
   search, a mail id, a thread load, a chat tool and a send.
2. **A grant opens only a shared row.** An INSERT of a grant on a private row fails.
3. **Each level does only its acts.** A `read` member gets 404 on send, draft and archive, and
   403 on a rule write. A `send` member gets 403 on a rule write and a grant write.
4. **A group grant follows membership.** Remove the member from the group, and the next read
   answers 404.
5. **Identity stays private.** Mail from the private box of a `send` member to `sales@` is not
   self (D-SM-5), and Reply-all keeps `sales@`.
6. **Offboarding.** The leaver's grants go. The mailbox and its mail stay. The purge plan leaves
   out the shared row. A removal that leaves no manager answers 409.
7. **The flag off.** With `EMAIL_SHARED_MAILBOX` unset, a member with a grant reads nothing of the
   shared row.
8. **Send log.** A send from `sales@` writes one row with the member. The app role cannot
   DELETE it, and cannot UPDATE any column but `provider_message_id`.
9. **Held card.** A card that sends from a private box does not read the Sent of `sales@` as
   `sent`.

A seam fence, `tests/unit/test_email_owner_predicate_seam.py`, counts the inline
`user_id = :uid` sites under `routes/email/` and fails when a new one appears without the marker
comment `# private-only:`.

---

## 9. Chat and UI

| Surface | What changes |
|---|---|
| **Mailbox switcher** | A "Shared" group under the private mailboxes. Each row has the chip, the label, the address and a people icon. |
| **All inboxes** | A shared mailbox is in All inboxes for a member with `send`. For `read` only, it is off by default, and the member can turn it on. The D-EM-28 switch "keep separate" works for a shared mailbox too, for each member. It needs a per-member row, so it is a column on a new `email_mailbox_member_prefs` table, not on `email_accounts`. |
| **List row** | The chip, and the assignee avatar when a thread has one |
| **Reading pane** | "In Sales · sales@fracktal.in", the assignee, "Priya is replying", and "I'll answer" |
| **Composer From picker** | The shared mailboxes with `send` after the private ones. A reply starts on the mailbox of the mail (D-EM-20). A read-only member never sees `sales@` in From. |
| **Chat scope picker** | Each shared mailbox with any grant, under "Shared". In All inboxes, the chat reads the shared mailboxes that All inboxes holds for that member. |
| **Chat binding (§11.3 order)** | Rule 3 ("the mailbox that last wrote to that recipient") reads only mailboxes the member can send from. Each card names "From Sales · sales@fracktal.in". |
| **AI settings** | A shared mailbox shows its settings read-only to `send` members, and editable to `manage`. |
| **Admin → Email** | "Shared mailboxes": add, grants, default assignee, connector record. Behind `email:shared:manage`. |
| **The 5-mailbox cap** | Counts private mailboxes only (Q-SM-5). The org cap of shared mailboxes shows on Admin → Email. |

**The agent.** `GET /email/accounts` adds `shared: bool` and `level: "read"|"send"|"manage"|null`
for each row. `_accounts` passes them on. `_one_mailbox` asks "Which mailbox?" when the member
reaches two or more mailboxes, a shared one included. A write tool refuses a mailbox below the
level it needs, and names the level.

---

## 10. Slices

Each slice is one PR. Each is HIGH tier: full loop, Opus review, an R8 suite on a real database.
Each ships dark behind `EMAIL_SHARED_MAILBOX` (default OFF). Run the database first:
`bash scripts/dev_db.sh` then `eval "$(bash scripts/dev_db.sh --export)"`.

| Id | Gate | What | Done when | Verification |
|---|---|---|---|---|
| **SM-0** | ✅ DONE 2026-10-23 | The owner answered §1 and accepted D-SM-1 to D-SM-9 | Each question has an answer in this file, and a WS row exists in `work_plan.md` §2 | Read §1 |
| **SM-1** | 🟢 AGENT-SAFE | The expand migration of §8.2 step 1, and the permission seed | A fresh ladder and an upgrade both apply it. The four tables have FORCE RLS. A grant on a private row fails. `test_tenant_coverage.py` passes. | `uv run pytest tests/unit/test_email_shared_mailbox_schema_r8.py tests/unit/test_tenant_coverage.py tests/unit/test_tenancy_boundary.py` |
| **SM-2** | 🟢 AGENT-SAFE | The seam of §8.1. `mailbox_access.py`, the three helpers call it, each inline site moves or gets `# private-only:`. Flag OFF gives the private arm only. | R8 fences 1, 4, 5 and 7 pass. The seam fence passes. No route answers differently with the flag off. | `uv run pytest tests/unit/test_email_shared_mailbox_r8.py tests/unit/test_email_owner_predicate_seam.py` then `uv run python scripts/affected_tests.py --run` |
| **SM-3** | 🟢 AGENT-SAFE | Make shared and grants. `POST /email/accounts/{id}/share`, `GET` and `PUT /email/accounts/{id}/grants`, D-SM-3 and D-SM-9, the refusal of Q-SM-7, the org cap of Q-SM-5 | R8 fences 2 and 3 pass. A non-holder of `email:shared:manage` gets 403. The last manager cannot leave. | `uv run pytest tests/unit/test_email_shared_grants_r8.py` |
| **SM-4** | 🟢 AGENT-SAFE | Send record and the level checks on each write: `email_send_log`, `drafted_by`, the 409 of §5.4 step 2, the scheduled-send check | R8 fence 8 passes. Two sends on one thread: the second answers 409 with the first reply. | `uv run pytest tests/unit/test_email_shared_send_r8.py tests/unit/test_email_send_later.py` |
| **SM-5** | 🟢 AGENT-SAFE | Assignment: the table use, claim, auto-claim, hand over, Reply Zero by assignee, clear on lost grant | A NEEDS_REPLY thread shows once for its assignee and for no other member. Unassigned shows to each `send` member. | `uv run pytest tests/unit/test_email_shared_assign_r8.py` |
| **SM-6** | 🟢 AGENT-SAFE | AI context: Mem0 scope `shared#acct:<id>`, the voice and signature of §6.1, the sign-off line, the rule rules of §6.3 | A draft by Priya in `sales@` reads no memory of `<priya>#acct:<private>`. A `send` member gets 403 on a rule write. | `uv run pytest tests/unit/test_email_shared_ai_context.py tests/unit/test_email_shared_rules_r8.py` |
| **SM-7** | 🟢 AGENT-SAFE | Held chat: `org` and `sent` of §6.2, and the agent fields of §9 | R8 fence 9 passes. `_one_mailbox` asks when a shared mailbox makes two. A read-only member gets a refusal that names `send`. | `uv run pytest tests/unit/test_email_held_card_shared_r8.py apps/agents/agent-email-assistant/tests` |
| **SM-8** | 🟢 AGENT-SAFE | Offboarding of §4.2: the purge scope, the grant removal, the 409 | R8 fence 6 passes. `test_email_mailbox_teardown.py` still passes. | `uv run pytest tests/unit/test_email_shared_offboard_r8.py tests/unit/test_email_mailbox_teardown.py` |
| **SM-9** | 🟢 AGENT-SAFE | UI of §9: switcher, All inboxes, From, chat scope, reading pane, Admin → Email. One look, per `DESIGN_SYSTEM.md`. | Visual review in light mode, compact density, a changed accent and a phone width. No raw palette class. | `cd workbench/control_plane && npx tsc --noEmit && npx vitest run` then the `visual-review` skill |
| **SM-10** | 🔴 OWNER-GATE | Turn on `EMAIL_SHARED_MAILBOX` in production, first for Fracktal only | The owner says so in chat. Staging test first (CLAUDE.md §4). | `curl -s https://api.dev.metorite.com/version`, then the acceptance of SM-3 to SM-8 on `dev.metorite.com` |
| **SM-11** | 🔴 OWNER-GATE | Q-SM-1 (b): delegated Exchange shared mailboxes, `/users/{addr}/` paths and the `.Shared` scopes | Only if the owner asks. A new Microsoft scope changes the consent screen of every customer. | Its own spec section, written when the owner asks |

**Order.** SM-1, then SM-2. SM-3 to SM-8 can run in parallel after SM-2, two at a time at most
(the dev box runs out of memory with more). SM-9 needs SM-3 and SM-5. SM-10 needs all of them.

**Owner gates.** SM-0 is the decisions. SM-10 is a flag that shows company mail to other members
in production. SM-11 is a new scope on the consent screen of each customer. CLAUDE.md §3a
rule 3 applies to SM-10: it changes who reads mail of a live organization, so the owner decides.

---

## 11. Edge cases

| # | Case | Result |
|---|---|---|
| 1 | Priya has a private copy of `sales@` and an admin makes another copy shared | Q-SM-7: the new private connect is refused. Her copy stays with a banner. |
| 2 | A member holds `send` through `org` and `read` through their email | The highest level wins |
| 3 | A grant names a group that does not exist | The validator accepts the grammar. The grant matches nobody until the group exists, as in Projects |
| 4 | The connector disconnects `sales@` | Only a `manage` member may. The disconnect dialog names the number of members who lose it |
| 5 | A rule in `sales@` forwards to Priya's private address | D-EM-29 does not apply, because Priya's address is not an own address of `sales@`. The loop guard of §11.6 edge case 12 applies |
| 6 | A chat in All inboxes, "reply to the last mail from Acme", and Acme wrote to both Priya and `sales@` | §11.3 rule 1: the mailbox of the mail. The card names the From |
| 7 | Two organizations connect `sales@fracktal.in` | Two rows, two tenants, no shared path (§2) |
| 8 | A member loses `feature:email` | Every shared grant stops at the seam (D-SM-4). The grants stay, and return with the feature |
