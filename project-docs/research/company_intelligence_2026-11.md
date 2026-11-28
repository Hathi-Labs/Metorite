# Company intelligence: what Metorite borrows and what it skips (2026-11-28)

**Status: research record.** It owns no work and no status. `specs/building_blocks.md` §10 (D235) owns the plan, and Metorite holds the state (D118). Epic: task 4150. Design: CI-0, task 4163.

**Who did the research.** metorite-78, on 2026-11-28, for the owner. The full reports are summarised in the comments of task 4150. Sources: the public repositories and papers named below, `origin/main`, and the production env files, read by flag name only.

**Check each fact against the code first.** A record describes the code and the products at its date.

---

## 1. The question

The owner asked on 2026-11-28 for a check of NVIDIA auto-ontology "or something like this". What is useful goes into the core of Metorite, its chat and its orchestration. Every source must be graphable and searchable, internal apps and outside integrations alike. Metorite must correlate, find causes and find risks across siloed data. It must remember what each member prefers and worries about. Later it must nudge the right person, by role.

Five rules bound the answer before the research started. D105: federate, never copy. D128: a graph of pointers, no model in retrieval, one search seam. D132: Graphiti, Neo4j and Mem0 retire. D205: extend a core block. R5: tenant-ready.

## 2. Outside projects

| Project | Licence | What it does | Verdict | The reason |
|---|---|---|---|---|
| **NVIDIA auto-ontology** | Apache-2.0, created 2026-04-27 | Builds a semantic layer over SQL databases for text-to-SQL | **Borrow the method. Do not install** | The method fits WS-SEM. Profile values in SQL, and keep stats, not rows. Keep a join only when the data confirms it. Group the same schema into term families. Certify, then keep. The app has no tenancy, and its default models are NVIDIA hosted ones, so a tenant's schema would leave the Router. It covers SQL only, not CRM, email or WhatsApp. Rows SEM-18 to SEM-21 |
| **Splink** | MIT | Probabilistic entity resolution (Fellegi-Sunter) in SQL | **Borrow the method** | It runs in SQL with no model and no egress, for each tenant. A high threshold keeps a false merge rare. Row KG-22 |
| **Palantir ontology** | Product | Objects, links and actions | **Borrow the words** | Metorite already has records, links and door actions. The same three words make a map from an outside source to the core types plain. Row SEM-20 |
| **Cube** | Apache-2.0 | Measures in a semantic layer | **Borrow the idea** | A measure in the meaning model (SEM-7) has the same shape |
| **OntoGPT** | BSD-3 | Extraction against a schema | **Borrow for pages only** | Page text has no schema, so a closed list of types helps there. A model runs at write time only (KG-10) |
| **GraphRAG** | MIT | Community summaries of a graph | **Borrow for pages only** | KG-13 clusters the graph by code. A summary for each cluster may come later, written at write time |
| **DoWhy** | MIT | Causal inference with refutation tests | **Borrow, with a human-drawn graph only** | Without a graph that a person draws, the tool gives a confident wrong cause. Refutation tests stop a weak claim. Row KG-28, a later pilot behind an owner gate |
| **LightRAG, Cognee** | MIT, Apache-2.0 | Graph RAG with their own stores | **Skip** | Each adds a second store, against D128 |
| **Neo4j LLM Graph Builder, Kuzu** | Apache-2.0, MIT | Graph stores | **Skip** | A second store. D128.6 keeps the graph in Postgres under FORCE ROW LEVEL SECURITY |
| **Apache AGE** | Apache-2.0 | A property graph inside Postgres | **Skip for now** | Plain tables `kb_entities` and `kb_links` serve one-hop and two-hop reads. ADR-002 is superseded by D128.6 |
| **Wren AI, Zingg** | AGPL-3.0 | A semantic layer, entity resolution | **Skip** | The licence. A read for patterns only, and no code |
| **Vanna, causal-learn** | MIT | Text-to-SQL with a vector store, automatic causal discovery | **Skip** | Vanna puts a model in retrieval. Automatic causal discovery gives claims that no person checked |

## 3. Memory systems

**Adopt no memory system.** Borrow four patterns.

| System | What Metorite borrows | Why it adopts none |
|---|---|---|
| **Mem0** | Extract, then ADD, UPDATE or DELETE against the stored facts, in one write path | Mem0 leaves isolation to the app. That is why it leaked between orgs (KG-9). D132 retires it |
| **Zep and Graphiti** | Supersede with `valid_from` and `valid_to`, never overwrite (PZ-22, PZ-56) | Both need a graph database |
| **Letta** | Small editable blocks that the member sees | Letta is a whole agent runtime |
| **Claude and ChatGPT memory** | The controls: view, edit, delete, and off | A product, not a library |

The member profile already has five kinds, a cap of 40, forget and export. It lacks a kind for worries (PZ-54) and a history (PZ-56). No org memory is live: `save_org_memory` writes to Mem0, which is off. `memory_architecture.md` §11 decides one home for each company term.

## 4. Proactive nudges

Little hard evidence exists. The vendor practice agrees on these points, and the rows of rung 6 follow them:

1. Start with one or two signals of high confidence (LOOP-28, LOOP-29).
2. The owner of the record first, then the lead, then an admin (LOOP-30).
3. A digest by default, a budget for each person, and no repeats (Reach §6, LOOP-31).
4. Show the evidence with each nudge (`finding_contract` evidence).
5. Measure the action rate, and retire a rule with a low rate (LOOP-32, LOOP-33).
6. A model words the message. It never picks the person.

## 5. What Metorite has today (2026-11-28)

**Live in production:**

- `GRAPH_SWEEP_ORGS=*` writes the `kb_links` edges. No code reads them: `graph.read_edges` has no caller.
- `FIND_ORGS=*` gives one-hop related records over live joins only.
- `COMPANY_SEARCH_ENABLED` searches 9 sources.
- The member profile, with 5 kinds and Assistant settings.
- `MY_DAY_LEARN_ORGS=*`.
- Reach for 2 orgs and 3 members.

**Dark or missing:**

- The risk kinds are built, and `MY_DAY_PROVIDERS` does not list them.
- No detector exists for an invoice or an SLA, and no detector reads more than one app.
- No kind holds worries, and no org memory is live.
- Reach cannot address a third person, and nothing picks a recipient by role.

**Drift that CI-0 repaired:** KG-3b, KG-4 and KG-5 were merged and marked open. ADR-002 had no "superseded" line. The retrieval docstring said seven sources.
