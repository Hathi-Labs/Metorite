# Flip a gateway flag with no restart

A flag flip reloads the gateway. It does not restart it. A restart ends the
chat runs in flight, and members see "Gateway error 502" for 10 to 25 s.

The code is `apps/services/gateway/gateway/flag_reload.py`. The fence is
`tests/unit/test_flag_reload.py`.

## The procedure

1. Make sure that the gateway has the reload. If `flag_reload` is not
   `true`, stop. A gateway without the handler ends on SIGHUP, and systemd
   then starts it again with no drain.

   ```bash
   curl -s http://127.0.0.1:8080/version
   ```

2. Make a backup of `/opt/acb/app/.env`. Then change one flag line.
3. Send SIGHUP to the main process only:

   ```bash
   sudo systemctl kill -s HUP --kill-whom=main acb-gateway
   ```

   ⚠️ Keep `--kill-whom=main`. Without it, systemd sends the signal to each
   process of the unit, and a child process of an agent ends on SIGHUP.

4. Read the result. The line names keys, never values.

   ```bash
   sudo journalctl -u acb-gateway -n 50 -o cat | grep flags.reload
   ```

   - `flags.reloaded applied=[...]`: those flags are live.
   - `restart=[...]`: those keys need a restart. Do the restart at a quiet
     time, and say why in the same message.
   - `flags.reload_refused`: `Settings` refused a value. Nothing changed. Fix
     the line and send SIGHUP again.

5. Check that `flags_reloaded_at` in `/version` moved, and that the process
   did not change (`systemctl show acb-gateway -p MainPID`).

## The keys that need a restart

A reload keeps the running value of these keys:

- A name with a secret or infrastructure word: `KEY`, `TOKEN`, `SECRET`,
  `PASSWORD`, `URL`, `HOST`, `PORT`, `PATH`, `ID`, `AUTH` and the others in
  `DENY_WORDS`.
- A name that starts with `DB_`, `REDIS_`, `NEXT_PUBLIC_` or `PYTHON`.
- `EMAIL_SYNC_ENABLED` and `WORKFLOW_SCHEDULER_ENABLED`. They decide at start
  whether a background loop runs.
- `LOG_LEVEL`, `ACB_ENV` and `DEPLOY_ENV`.

## Why a reload is enough

Each flag reader calls `get_settings()` or reads `os.environ` when it runs.
Each flag parser caches on the raw value. The fence fails when a module keeps
a settings value from its import, or when a new cached function reads the
settings.

The reload covers the gateway only. The workbench, the agent edge and the
customer console still read their flags at start.
