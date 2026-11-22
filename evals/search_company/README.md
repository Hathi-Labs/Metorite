# The retrieval eval of search_company (WS-KG KG-1)

This folder holds an eval of `GET /search/company`. It asks the route a fixed set
of questions and scores where the right record lands. No model runs. No
embedding call goes out, because `KNOWLEDGE_EMBED` stays off.

The owning text is the KG-1 row of `project-docs/specs/company_context.md` §12.
That row replaces CX-2. The design of the set comes from §9 of that spec.
`project-docs/specs/knowledge_graph.md` §7 (KG-0) sets the targets that the
later KG slices measure with this eval.

**Status.** Built on 2026-10-28. The fixture run and the R8 suite pass. The run on
the real set is a separate call on staging. Its baseline is a comment on the
Metorite epic 783.

## The files

| File | What it holds |
|---|---|
| `fixtures/synthetic.json` | 70 synthetic records and 50 questions. A question names its gold record by key |
| `questions.py` | The loaders, the check that the set is whole, and `gold_id` (the id that the seed gives a key in one org) |
| `seed.py` | The records as SQL for one org, and `purge`. It opens no connection |
| `score.py` | Recall, percentiles, the empty-source report, the verdict and the text of the report. No I/O |
| `run.py` | The runner: `--check`, `--fixture` and `--live` |
| `fixtures/misses.json` | The misses set (WS-FIND FIND-1): 20 synthetic cases that search misses today. See "The misses set" |

An agent invented each name. No row comes from a real person, company or mail.

## What it measures

- **recall@5.** A question is a hit when a gold id is among the first 5 hits of
  its own source in the answer. The route cuts each source at `k=5`, so the
  number says whether the right record reached the member.
- **merged@5.** The harder cut: a gold id is among the first 5 hits of the whole
  answer, after the fusion of the sources.
- **p50 and p95.** The time of one request, by nearest rank:
  `sorted[ceil(p / 100 * n) - 1]`. There is no interpolation.
- **Empty sources.** A source of the registry with no hit for any question, and
  every name in `skipped`. A source that has questions and no hit for them is a
  FAILING source.

## The modes

| Mode | What it does | Needs |
|---|---|---|
| `--check` | Checks that the synthetic set is whole (50 questions, 10 for each source, every gold key seeded) and that the misses set is whole (20 cases). | Nothing. CI runs it in `skill-eval.yml` |
| `--fixture` | Seeds a fresh org in `TENANT_LADDER_DATABASE_URL`, runs the 50 questions through the route in this process, prints the score, removes the rows. | A local ladder database (`scripts/dev_db.sh`) |
| `--live URL --as EMAIL --set PATH` | Asks a running gateway over HTTP as the member EMAIL. | `GATEWAY_INTERNAL_TOKEN` in the environment, and the real set |

`--fixture` exits 0 when every source that has questions returns a hit for them
and recall is at least the floor of **0.90**. Since KG-3b it also needs a p50
under **1.5 s** and a p95 under **3 s** (`knowledge_graph.md` §7.3). It exits 1
otherwise, and 2 when it cannot start. A source without questions (`whatsapp`, `artifacts`) shows as
empty and does not fail the run.

**The floor.** The synthetic baseline is recall 1.000 and merged@5 1.000,
because each question holds words of its gold record. So 0.90 is not a quality
bar. It leaves room for one drift of the fusion in ten, and no more. A change to
ranking runs the eval before and after, and writes both numbers in the pull
request (`company_context.md` §9).

The R8 suite `tests/unit/test_search_company_eval_r8.py` runs the same code as
the non-privileged role. It fails when a source returns nothing for its 10
questions, in two ways: a closed CRM switch and an empty Knowledge index. It
also shows that a member of another org finds none of the gold ids.

## Run it

```bash
uv run python -m evals.search_company.run --check
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run python -m evals.search_company.run --fixture
```

## The misses set (WS-FIND FIND-1)

`fixtures/misses.json` holds 20 synthetic cases. Each keeps the shape of a real
failure of `search_company` (`project-docs/specs/find_engine.md` §1). Each case
names three things:

- `kind`: `near_name`, `words_spread`, `related_only`, `converted_lead`,
  `past_the_cut`, `past_chat` or `outside`.
- `rung`: the lowest rung of `find_engine.md` §3.1 that finds it.
- `slice`: the slice that should turn it green.

`--fixture` seeds the misses into a second org, so they never crowd the 50
questions. It then prints one more block, for example
`misses: 0/20 found in the top 5 of one call`, with the score by kind and by rung.
A case counts when its gold record is among the first 5 hits of the whole answer.

- The misses score does not gate the run. It is the number that each slice moves.
- Each slice of `find_engine.md` §9.2 prints the score before and after in its
  pull request. A drop does not merge. `MISSES_BASELINE` in `score.py` holds the
  floor, and `test_the_misses_score_does_not_drop` in the R8 suite reads it.
  A slice raises the constant to the number that it reaches.
- The end of rung 2 scores 18 of 20 or more. The two `outside` cases wait for FIND-10.
- The cases `past_chat` and `outside` have no seed, because no table holds a
  past chat or an outside app yet. They miss by construction. HRD-21 and FIND-10
  add the seed.
- The real misses of production stay off the repo, in `~/.metorite/evals/kg1/`.

## The real set (customer data)

Staging holds customer data (DPDP). So the real set stays out of git.

- The set is a file in `~/.metorite/evals/kg1/`, mode 600, in no worktree. Its
  path is `KG1_REAL_SET` or `--set`. The runner refuses a file that others can read.
- Its shape is `{"questions": [{"id", "source", "text", "gold": ["<record id>"]}]}`.
  Gold is by id. `source` is one of `email`, `crm`, `tasks`, `knowledge`, `people`.
- With no file, `--live` prints `no real set` and exits 2.
- The URL must be a loopback address, so the run reads a gateway on this machine
  and never production. The run only reads. It writes nothing.
- The token comes from `GATEWAY_INTERNAL_TOKEN`. The runner never prints it.
- The report holds counts, ratios, seconds, source names and question ids. It
  never holds the words of a question, a title or a snippet. A failed request
  shows as an error count, with no message.
- When an org gates a source off (for example CRM or Knowledge), it shows
  as an empty source. The run does not fail on it and does not flip a switch.
- The gateway reads `CONTEXT_RECENCY` and `COMPANY_SEARCH_ENABLED`. The runner
  cannot see them over HTTP. The caller passes them as `--flag NAME=VALUE`, and
  the report prints them. The fixture mode reads them from the settings.

```bash
KG1_REAL_SET=~/.metorite/evals/kg1/set.json GATEWAY_INTERNAL_TOKEN=... \
  uv run python -m evals.search_company.run --live http://127.0.0.1:8080 \
  --as member@example.test --flag CONTEXT_RECENCY=off --flag COMPANY_SEARCH_ENABLED=on
```

## What it does not do

It changes no ranking, no decay and no digest. It edits nothing under
`gateway/retrieval/`. A change to the score maths must keep
`tests/unit/test_search_company_eval.py` green. That test fails on a mutation of
`k`, of the percentile rank and of the source filter.
