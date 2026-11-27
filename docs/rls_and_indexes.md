# Row level security and indexes

**Rule.** Under FORCE ROW LEVEL SECURITY, Postgres uses a clause as an index
condition only when each function in it is LEAKPROOF. The tenant policy runs
first, and a clause that is not leakproof must wait for it. So an index on an
expression helps a superuser or a BYPASSRLS role, and it does not help the app
role.

**What is not leakproof.** `to_tsvector`, `websearch_to_tsquery`, `@@`, `||`,
`->>` and `ILIKE` are not. To mark a function LEAKPROOF needs a superuser. A
Supabase project does not give one, so do not plan on it.

**`numeric` is not leakproof.** In Postgres 16 each `numeric` comparison
(`=`, `<>`, `<`, `<=`, `>`, `>=`) is not leakproof. So an index on a `numeric`
column gives the app role no index condition. The comparisons of `text`,
`timestamptz`, `uuid`, `boolean` and the integer types are leakproof. An
index still gives its order to a sort. WS-STU C2a measured it on
`studio_record_hot.v_num` (`tests/unit/test_studio_query_r8.py`).

**Measured (KG-3a, 2026-10-28).** On staging, as `acb_app`, the email search
parsed each of 7243 bodies, and one COUNT took 2.5 to 5 seconds. On 20000
ladder rows it took 0.46 ms as a superuser and 5.8 seconds as the app role.

**The pattern: a stored column.** Store the value that the clause computes,
and test the column. Migration 314 adds `email_messages.fts`, and a trigger
fills it. The search reads `COALESCE(em.fts, <the expression>)`, so a row that
the backfill has not reached gives the same answer. The scan stays, but the
test of each row is cheap: 0.07 to 0.15 seconds for 20000 rows.

- Add the column nullable, with no default. A STORED generated column rewrites
  the table under an ACCESS EXCLUSIVE lock (16.5 seconds on 20000 rows).
- Fill the old rows with a batched script through the tenant seam
  (`scripts/backfill_email_fts.py`). Do not fill them in the migration.
- Keep one expression. The trigger, the backfill and the search must agree.
- Do not add a SECURITY DEFINER function that applies the tenant predicate
  itself. That is a second tenant seam (D15, R5).

**Probably affected, and not checked.** `/projects/search` uses `ILIKE` with
the trigram indexes of `170_projects_search_trgm.sql`. `ILIKE` is not
leakproof, so the app role probably scans `pm_tasks`. Test it with
`EXPLAIN (ANALYZE)` as the app role.

**Production may differ.** A role that bypasses RLS gets the index. Nobody has
checked the role of the production gateway.

**How to check a query.** Run `EXPLAIN (ANALYZE)` as the app role, with the
tenant bound by `set_config`. A superuser skips the policy and sees an index
that the app never gets.
