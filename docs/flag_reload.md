# Flip a per-org flag with no restart

A flip of a per-org flag reloads the gateway. It does not restart it. A
restart ends the chat runs in flight, and members see "Gateway error 502"
for 10 to 25 s. Every other flag still needs a restart.

The code is `apps/services/gateway/gateway/flag_reload.py`. The fence is
`tests/unit/test_flag_reload.py`.

## The flags that reload

A reload applies only the `*_ORGS` and `*_MODES` fields of `Settings`. These
are the per-org rollout lists, such as `CRM_ORGS`, `TOOL_PACKS_ORGS` and
`DECIDE_FEATURE_MODES`. Each reader of them reads the value again on each
call.

A reload does NOT apply any other key. The log names each one under
`restart`. That includes:

- an on/off switch such as `CALENDAR_REMINDERS`, `JOB_QUEUE` or
  `WEB_PUSH_ENABLED`. A background loop decides at start whether it runs.
- a security gate such as `V1_ALLOW_CALLER_ENDPOINT_OVERRIDE`. A module
  copies it at import.
- each secret, URL, port and password, including `PGPASSWORD`.
- each `DB_` and `REDIS_` setting.

## The procedure

1. Make sure that the gateway has the reload. If `flag_reload` is not
   `true`, stop. A gateway without the handler ends on SIGHUP, and systemd
   then starts it again with no drain.

   ```bash
   curl -s http://127.0.0.1:8080/version
   ```

2. Make a backup of `/opt/acb/app/.env`. Then change one `*_ORGS` or
   `*_MODES` line.
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
   - `restart=[...]`: those keys changed in the file and need a restart. Do
     the restart at a quiet time, and say why in the same message.
   - `flags.reload_refused`: the new settings did not validate. Nothing
     changed. Fix the line and send SIGHUP again.

5. Check that the process did not change
   (`systemctl show acb-gateway -p MainPID`). `flags_reloaded_at` in
   `/version` moves only when a flag was applied.

## How the reload keeps other keys as they are

The reload builds the new settings from the RUNNING settings plus the
applied flags. It does not read the environment or the env file again. So a
key that the file gained after the start stays dormant until a restart.

## Fences

- No module reads a reloadable flag at import.
- No start function of the gateway lifespan reads one. The fence resolves
  calls as Python does, three calls deep. A loop that a start function
  spawns reads its flags on each tick, so the fence does not enter it.
- No module keeps `get_settings()` in a module name, and no new cached
  function reads the settings.

The reload covers the gateway only. The workbench, the agent edge and the
customer console still read their flags at start.
