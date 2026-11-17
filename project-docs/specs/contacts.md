# Contacts: the people a member writes to, then a Contacts app

**Status: PROPOSED, 2026-11-17. Verified against the code on 2026-11-17
(`origin/main` at `cf9cbb5d9`).** Epic WS-CT, task 3591. CT-1 (task 3602) is
in build, and the other slices wait in Backlog.

§6 holds three questions for the owner. CT-7 and CT-8 wait on them.

**The seam owner.** `building_blocks.md` (the row "Outside people") and D174.7
say that `crm_contacts` is the one store of outside people. This spec keeps
that rule. The mail contact list of §4 is an index of a mailbox. Mail builds
it, and a refresh can build it again from the mail. It is not a second
contact store (D-CT-1).

**Owner ask, 2026-11-17, in chat.** The words are the owner's, so they sit in
a block that the linter does not read:

```text
"while searching for contacts, for whatever reason, the email ID was not
immediately found. I think this is probably a gap in our own implementation.
We need to have some sort of system where we are able to properly store the
contacts' email addresses, so we know the entire contacts. Probably they
should become a separate app in itself, where your own contacts or a personal
CRM can be created. But for the time being, have at least a basic system
where we are able to take note of all the contacts and email IDs from the
sent emails, along with the individuals' names, so that we can refer to them
whenever we want. This can later extend into a contact app by itself.
Architect this out and plan this out."
```

---

## 1. Scope and non-goals

**In scope.**

1. The chat finds a person that the member wrote to, by the name the member
   says, in one call (CT-1).
2. A stored list of the people each mailbox writes to (CT-2, CT-3). Each row
   holds the name, how often the member wrote, and when.
3. A People view in the Email app, where the member sees the list and
   corrects a name (CT-4).
4. Chat questions about the list, such as "who do I write to at Vodafone
   Idea?" (CT-5).
5. The plan for a Contacts app (CT-7), after the owner answers Q1.

**Not in scope.**

- A second store of outside people before the owner answers Q1.
- Phone contacts, WhatsApp contacts and the address books of Google and
  Microsoft. CT-8 plans the import, and it is OWNER-GATE.
- One member's contacts in the lookup of another member (Q2).
- Search over the text of mail. `find_engine.md` owns that.

---

## 2. What went wrong (2026-11-16)

A member of Shaurrya Teleservices asked the chat to write to colleagues and
to assign them tasks. The addresses were in his mail. The chat asked him for
them each time.

| What he said | Tool and answer | What his mail holds |
|---|---|---|
| "Rajnish Nimbark" | `resolve_person`: "Nobody matches. Ask for the full name or the address." | Rajnish Nimbark, 28 sent mails |
| "Vidyadhar Nayak" | `find_contact`: "No person matches" | Bidyadhar Nayak, 282 sent mails |
| "Sanjeev Goyal" | `find_contact`: "No person matches" | Sanjeev Goel, 292 sent mails |
| "Vijay Varda" | `find_contact`: "No person matches" | Vijay Raghav Varada |
| "Pankaj Gaurav" | `resolve_person`: "Nobody matches", three times | Pankaj Gaurav, 199 sent mails |
| "Payal Dave", "Nikita" | `resolve_person`: "Nobody matches" | Both, in his sent mail |

**Three gaps caused it.** The data was never the problem.

1. **`resolve_person` reads members and People only.** These colleagues are
   not Metorite members. The tool never reads the mail.
2. **`find_contact` matches the whole phrase.** "Vidyadhar Nayak" is not a
   substring of "Bidyadhar Nayak". The near-spelling pass
   (`_fuzzy_org_contacts`) scores members and People only. The one matcher
   (`acb_common.name_match`) gives 0.75, 0.61 and 0.72 for the three pairs.
   Its bar (`NEAR_SCORE`) is 0.6. So the near pass finds them when it reads
   the mail.
3. **No list exists.** His mailbox holds 593 distinct people he wrote to.
   `email_contacts` holds 1 row for his mailbox, and 9 rows in all of
   production. `email_senders` holds 21 rows for his mailbox. Each lookup
   builds "people I wrote to" again from the raw messages.

---

## 3. What exists today

| Part | Path | What it holds or does | Gap |
|---|---|---|---|
| `email_contacts` | `infra/postgres/119_email_contacts.sql` | One row for each (mailbox, address): name, title, company, phones and links from a signature. `manual_fields` keeps a person's edits. Tenant-scoped (`infra/postgres/generated/`) | Written only when a contact card opens. Holds no count and no date (migration 119 chose that) |
| `email_senders` | `infra/postgres/22_email_categorization.sql` | One row for each sender that the categorizer saw | Senders only. Few rows |
| `GET /email/contacts/suggest` | `gateway/routes/email/transport/contacts.py` | The typeahead and the door of `find_contact`. Sources `org`, `sent` and `seen`. The `sent` arm reads every message of the mailbox and keeps the sent ones | Whole-phrase match. Near pass over members only. About 130 ms warm on a mailbox of 37,000 messages |
| Near senders (HRD-18b) | `contacts.py`, `near_senders` | The near spelling of a sender, for `search_company` | Senders only, not the people the member wrote to |
| `resolve_person` (chat) | `acb_skills/profile_tools.py`, route `/auth/me/assistant-profile/resolve-person` | Aliases, then People and members | No mail tier |
| `find_contact` (chat) | `apps/agents/agent-email-assistant/agents.py` | Calls the suggest door. Also the contact card | Lives in the email pack, so the chat must load mail to use it |
| `crm_contacts` | `infra/postgres/144_crm.sql` | The company's outside people. `source` can be `email` | No private visibility |
| `wa_contacts` | `infra/postgres/102_whatsapp.sql` | WhatsApp contacts | Not linked to mail |

**A trap.** `email_ingestion/persist.py` `upsert_message` runs for each
message of each sync, also for messages that the mailbox already holds.
On 2026-10-28 it ran 33 million times in nine days. A write per message in
that path costs too much. So the list refreshes in batches (§4.3).

---

## 4. Design

### 4.1 The seam

- **The list is an index of a mailbox (D-CT-1).** It lives in
  `email_contacts`, the table that migration 119 made for "a Contacts view
  ... a people picker". No new contact table.
- **A row belongs to one mailbox (D-CT-2).** Every reader reads it through
  `mailbox_predicate`, the rule that decides who reads the mailbox. So a
  colleague never sees the people another member writes to. A shared
  mailbox shows its list to the members who read that mailbox. A removed
  mailbox removes its rows (`ON DELETE CASCADE`).
- **`crm_contacts` stays the one store of outside people (D174.7).** A
  contact enters the CRM only when a person saves it there (CT-6).

### 4.2 The list (CT-2)

`email_contacts` gains these columns. Each one is nullable or has a default,
so the code that runs now still works (R6). The migration takes the next free
number at build time (R1).

| Column | Type | Meaning |
|---|---|---|
| `sent_count` | `integer NOT NULL DEFAULT 0` | Mails of this mailbox that went to the address (To, Cc or Bcc) |
| `received_count` | `integer NOT NULL DEFAULT 0` | Mails from the address to this mailbox |
| `first_seen_at` | `timestamptz` | The date of the first such mail |
| `last_sent_at` | `timestamptz` | The date of the newest mail to the address |
| `last_received_at` | `timestamptz` | The date of the newest mail from the address |
| `header_name` | `text` | The name that the mail headers give most often |
| `names` | `text[] NOT NULL DEFAULT '{}'` | Up to 5 other names that the headers gave |
| `hidden` | `boolean NOT NULL DEFAULT false` | The member hid this row from the list |

**Counts are stored (D-CT-3).** Migration 119 kept counts out, because a
live query is right at once. That choice made each lookup scan the mailbox,
and it gave the list no order. The refresh of §4.3 recounts each changed
address from the mail, so a stored count is exact after each refresh.

`source` gains the value `mail` (a wider CHECK, so old rows stay valid). A
row that the refresh makes has `source = 'mail'`.

**A contact is an address that the member wrote to (D-CT-5).** The list
shows a row with `sent_count > 0` by default. An address that only wrote to
the member stays in the lookup as `seen`. The list shows it only under the
filter "Wrote to me".

### 4.3 The one writer

`refresh_contacts(db, account_id)` in the email app is the one writer of the
columns of §4.2. A fence asserts that no other code writes them (R7).

1. It reads the messages of the mailbox that sync stored after the mark of
   the last refresh. The mark is a new nullable column
   `email_accounts.contacts_refreshed_at`.
2. It collects the addresses of those messages. A message in `sent` gives
   its To, Cc and Bcc. Any other message gives its From.
3. For those addresses only, it recounts the figures of §4.2. One query
   reads the mail of the mailbox, and the writer upserts the rows.
4. It leaves `display_name`, `title`, `organization`, `phones`, `links`,
   `manual_fields` and `source = 'signature'` rows as they are.
5. It moves the mark.

**When it runs.** The email scheduler calls it after each sync batch of a
mailbox, at most once in 10 minutes for each mailbox. The send path calls it
for the addresses of the mail that it sent. A backfill runs it once for each
mailbox with no mark. A weekly run with no mark corrects any drift.

**Names (D-CT-4).** The name that a reader shows is, in this order: a name
that the member typed (`manual_fields`), the signature name, then
`header_name`. A header name in the form "Goyal, Ramesh (MUM), Vodafone Idea"
reads as "Ramesh Goyal". A header name that is only the address does not
count as a name.

### 4.4 The readers

| Reader | After CT-3 |
|---|---|
| `GET /email/contacts/suggest` | Reads the list in place of the scan of the sent mail. The near pass reads the list too. The `org` arm stays |
| `resolve_person` (mail tier, CT-1) | Reads the same door. Each hit says "from your mail, not a member of Metorite" |
| `find_contact` | Unchanged. It calls the suggest door |
| The People view (CT-4) | Reads the list, sorted by `last_sent_at` |
| `resolve_name` (FIND-5, `find_engine.md` §5) | The `near` ability of the email source reads the list. FIND-5 adds no second reader |
| KG-5 (task 790) | Can map an address to a name from the list. This spec builds nothing for it |

`resolve_person` is in the core tools, and `find_contact` is in the email
pack. A chat that loads the email pack loses its external apps today (task
3604). With CT-1, the chat finds a colleague from the mail with no email pack.

### 4.5 The Contacts app (CT-7, after Q1)

The recommended shape (Q1 option A) is a lens. One row stands for one
person, and it shows what each store knows:

- the colleague (members and People),
- the mail list of each mailbox that the member reads (`email_contacts`),
- the company contact (`crm_contacts`),
- the WhatsApp contact (`wa_contacts`).

Rows join by address and by phone at read time. "Save to CRM" (CT-6) copies a
mail contact into `crm_contacts`. A note or a tag of a person lives on the
CRM row. A private contact needs a visibility on `crm_contacts` (D12: private,
Center, org). The app then needs a shell manifest (R9, `navigation_shell.md`
§5).

---

## 5. The slices

Rungs follow `orchestration_playbook.md` §6d. Each rung ends in a demo that
the owner sees on production.

| Slice | Task | Rung | What | Done when | Fence and verification | Tier |
|---|---|---|---|---|---|---|
| **CT-1** | 3602 | 1 | The lookup reads the mail. The near pass of `suggest` covers the people the member wrote to and the senders. `resolve_person` gains a mail tier. No migration | The criteria of task 3602. "Email Vidyadhar Nayak" offers Bidyadhar Nayak as a near match, with no question for the address first | The three real name pairs as unit cases. An R8 test: member A never sees the mail list of member B, and org B never sees org A | STANDARD · AGENT-SAFE |
| **CT-2** | 3607 | 2 | The columns of §4.2, `email_accounts.contacts_refreshed_at`, the one writer of §4.3, the scheduler call, the send call and the backfill script | After the backfill on a copy of production, the mailbox of the 2026-11-16 member holds one row for each of the 593 people he wrote to, with exact counts. A second sync of the same mail changes no count | `tests/unit/test_contact_list_writer_r8.py`, new: the refresh counts once, a re-keyed message counts once, a second mailbox and a second org stay apart. `tests/unit/test_one_contact_store.py`, new: no new table name with `contact` outside `crm_contacts`, `crm_deal_contacts`, `email_contacts` and `wa_contacts`, and no writer of the §4.2 columns outside the one writer | STANDARD · AGENT-SAFE |
| **CT-3** | 3608 | 2 | `suggest`, the near pass and the mail tier of `resolve_person` read the list. The scan of the sent mail stays only as a fallback for a mailbox with no mark | `suggest` answers from the list in under 50 ms on that mailbox (`EXPLAIN (ANALYZE, BUFFERS)` on production) | The CT-1 tests stay green. An R8 test proves the fallback for a mailbox with no mark | STANDARD · AGENT-SAFE |
| **CT-4** | 3609 | 2 (demo) | A People view in the Email app: the list, sorted by the newest mail, a filter by company domain, "Wrote to me" as a second filter, a name that the member corrects (`manual_fields`), and Hide | The member opens Email → People, sees the people he writes to with their names, corrects one name, and the chat uses the new name | Vitest for the view. An R8 test for the name edit. Captures in light mode, compact density and at 390 px (`visual-review`) | STANDARD · AGENT-SAFE |
| **CT-5** | 3610 | 3 | The chat reads the list. `find_contact` with a domain and no name lists the people at that domain. The contact card shows the counts from the list | "Who do I write to at Vodafone Idea?" lists them with their addresses | `tests/unit/test_find_contact_domain_list.py`, new | STANDARD · AGENT-SAFE |
| **CT-6** | 3611 | 3 | "Save to CRM": the member saves a mail contact as a `crm_contacts` row, from the People view and from the chat (a card) | A saved contact shows in the CRM with source `email`, and a second save finds the first row | An R8 test of the save. It waits on Q1 only for the visibility of the row | STANDARD · OWNER-GATE until Q1 |
| **CT-7** | 3612 | Later | The Contacts app of §4.5 | Q1 answered, then its own spec section with a shell manifest | Set when Q1 is answered | OWNER-GATE (Q1) |
| **CT-8** | 3613 | Later | Import the address book of Microsoft 365 and Google | A new OAuth scope needs consent, and the Google scope needs a new verification | Set when the owner opens it | OWNER-GATE (Q3) |

**Order.** CT-1 first, because it fixes the chat with no migration. CT-2,
then CT-3 and CT-4 in parallel. CT-5 after CT-3.

---

## 6. Questions for the owner

**Q1. Where does a contact that a member keeps live?**

- **A (recommended).** The Contacts app is a lens over the stores that exist.
  A person that the member keeps becomes a `crm_contacts` row, with a new
  private visibility. This keeps D174.7: one store of outside people.
- **B.** A personal contacts table of its own. This amends D174.7, and the
  CRM and the Contacts app then hold two copies of a person.

**Q2. May one member's mail list help another member's lookup?** For
example, "Payal wrote to this person" shows for a colleague. Recommended:
no. A mailbox is private, and a list of correspondents is personal data.

**Q3. May Metorite read the address book of Microsoft 365 and Google
(CT-8)?** It needs a new consent from each customer, and Google needs a new
verification. Recommended: later, after CT-4 shows how much the mail list
already gives.

---

## 7. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_contact_list_writer_r8.py \
  tests/unit/test_one_contact_store.py -q
timeout 1200 uv run python scripts/affected_tests.py --run
cd workbench/control_plane && npx tsc --noEmit && npx vitest related <files> --run
```

On production, after CT-2 and its backfill, for the mailbox of one member:

```sql
SELECT count(*) FILTER (WHERE sent_count > 0) AS wrote_to,
       count(*) AS all_rows
  FROM email_contacts WHERE account_id = '<mailbox id>';
```

---

## 8. Decisions of this spec

- **D-CT-1.** The mail contact list is an index of a mailbox, in
  `email_contacts`. It is not a contact store. `crm_contacts` stays the one
  store of outside people (D174.7).
- **D-CT-2.** A row of the list belongs to one mailbox, and each reader reads
  it through `mailbox_predicate`.
- **D-CT-3.** Counts and dates are stored. One refresh for each mailbox
  recounts the changed addresses. No write happens for each message at
  ingest.
- **D-CT-4.** A name shows in this order: typed by the member, from the
  signature, from the headers.
- **D-CT-5.** A contact is an address that the member wrote to. An address
  that only wrote to the member is found by a lookup, and the list shows it
  under a filter.
