# Metorite test suite

The app-wide automated test suite. Grows alongside features — every new
capability should land with tests here.

## Layout

```
tests/
  unit/          fast, no network/DB; mock provider + DB + LLM.   ← CI-gated
  integration/   @pytest.mark.integration; need the docker stack. ← opt-in
workbench/control_plane/e2e/   Playwright end-to-end (frontend).
```

## Conventions

- **Framework:** `pytest` with `pytest-asyncio` in `asyncio_mode = "auto"`
  (config in root `pyproject.toml` → `[tool.pytest.ini_options]`). Plain
  `async def test_*` works without a decorator.
- **Mocking:** `unittest.mock.AsyncMock` + `patch` (no `respx` in the stack).
- **FastAPI routes:** `from fastapi.testclient import TestClient` (see
  `tests/unit/test_memory_e2e.py`).
- **No live DB in unit tests:** mock the SQLAlchemy session (`AsyncMock`) and
  assert the calls / dispatch logic, or test pure helpers directly.
- **Markers:** `integration` (docker stack), `slow` (long-running). The default
  run excludes integration (`-m 'not integration'`).

## Running

```bash
uv run pytest tests/unit/ -q          # fast unit run (what CI gates on)
uv run pytest -q                      # everything except integration
uv run pytest -m integration -q       # integration (needs docker compose up)
make cov                              # coverage over apps/ + packages/
```

CI runs `uv run python -m pytest tests/unit/ -x -v` on every PR
(`.github/workflows/pr-check.yml`).

## Email automation tests

The email app is the most actively tested area (inbox-zero parity work — see
`project-docs/specs/archive/email_inbox_zero_parity_plan.md` §7). Files:

| File | Covers |
|---|---|
| `test_email_folders.py` | folder-name normalization |
| `test_email_triage.py` | orchestrator triage |
| `test_email_rules_engine.py` | rule action dispatch (`_apply_rule_actions`) |
| `test_email_webhook.py` | Graph push: validation handshake + notification routing |
| `test_email_assistant_settings.py` | settings model round-trip |
| `test_email_categorization.py` | sender auto-categorization hook |

These mock the email provider and DB session, so they run with no mailbox and
no network. They double as **regression guards** for the inbox-zero features as
we extend them.
