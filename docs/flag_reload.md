# Flip a per-org flag with no restart

A flip of a per-org flag reloads the gateway. It does not restart it. A
restart ends the chat runs in flight, and members see "Gateway error 502"
for 10 to 25 s. Every other flag still needs a restart.

The code is `apps/services/gateway/gateway/flag_reload.py`. The fence is
`tests/unit/test_flag_reload.py`.

## The flags that reload

A reload applies only the `*_ORGS` and `*_MODES` fields of `Settings`. These
are the per-org rollout lists, such as `CRM_ORGS`, `TOOL_PACKS_ORGS`,
`AGENT_QUESTIONS_ORGS` and `DECIDE_FEATURE_MODES`. Each reader of them reads
the value again on each call.

`AGENT_QUESTIONS_ORGS` changes who may answer an agent's question
(`external_agent_access.md` §12.8). A reload changes how the flip applies, and
it does not change who may flip it. The flip stays OWNER-GATE
(`enforcement-flip`).

`WAITING_ORGS` opens `GET /shell/waiting` and `GET /shell/waiting/count`
(`waiting_on_me.md` §7.3). Its one reader is
`gateway.routes.shell.waiting.waiting_allowed`. A reload applies a change. The
flip stays OWNER-GATE (`enforcement-flip`).

`AGENTS_VIEW_ORGS` opens the member view of agents in Projects
(`agents_view.md` §6, AGA-6). Off, `GET /projects/agents/overview` answers
404. Its one reader is
`gateway.routes.projects.agent_summary.agents_view_allowed`. A reload applies
a change. The flip stays OWNER-GATE (`enforcement-flip`).

`GITHUB_APP_ORGS` turns the Metorite GitHub App on for each named org
(`agent_coordination.md` SW-53). `GITHUB_APP_ENABLED` stays the master, and a
reload does not apply the master. With the master on, an empty list turns
every org off. Its one reader is
`gateway.connections.github_app.github_app_org_allowed`. The webhook, the
connect routes, the poll and `github.installation_of` ask it, so the lazy
readers follow the list too. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`WORKFLOW_STUDIO_GATE_ORGS` turns on the authoring gate of `/workflows`
(`ai_studio.md` §14 J1-3). For each listed org, a workflow write needs
`feature:studio` on top of `feature:workflows`. `*` lists every org. Its one
reader is `gateway.routes.workflows.core.author_gate_on`, and each request
asks it. A reload applies a change, and the flip stays OWNER-GATE
(`enforcement-flip`).

`STUDIO_DOOR_OPS_ORGS` opens the door op of the Studio action door
(`ai_studio.md` §14 B2b-2). For each listed org, a class A `<app>.<op>`
action of a package runs that op of the app, as the caller. `*` lists every
org, and the default is empty. Its one reader is
`gateway.routes.studio.flags.door_ops_allowed`, and each request asks it. The
door also needs `STUDIO_ORGS`. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_LOGIC_ORGS` opens the logic module of the Studio action door
(`ai_studio.md` §14 B4a). For each listed org, a `logic:<name>` action of a
package runs a first-party module of the gateway. `*` lists every org, and the
default is empty. Its one reader is `gateway.routes.studio.flags.logic_allowed`,
and each request asks it. The registry of modules ships empty, so the flag opens
nothing until a module registers. A reload applies a change, and the flip stays
OWNER-GATE (`enforcement-flip`).

`STUDIO_NUM_F8_ORGS` adds the float8 test to a number filter of the Studio
object query (`ai_studio.md` §14 C2c). For each listed org, the query tests
`studio_record_hot.v_num_f8` beside the exact `v_num` test, so the app role
can use the index. `*` lists every org, and the default is empty. Its one
reader is `acb_common.studio_flags.num_f8_allowed`, and each query asks
it. The query honours it for an org only after the backfill of migration 433 ends (`_warm_f8`), so the records are right at every step. Runbook: after a restore or any trigger work (`DISABLE TRIGGER`, `session_replication_role = replica`, `pg_restore --disable-triggers`), restart the gateway before you trust the flag, because a ready answer is cached for up to 600 seconds.
A reload applies a change, and the flip stays OWNER-GATE (`enforcement-flip`).

`AGENT_WRITE_CAP` and `AGENT_WRITE_WINDOW_S` set the write cap of each agent
connection (`external_agent_access.md` §9.2, MCP-G20). The defaults are 120
writes in 600 s. They are not `*_ORGS` or `*_MODES` keys, so a reload does
NOT apply them. The gateway counts the writes, and the agent edge names the
numbers in each write tool. Set both units to the same value. Then restart
`acb-gateway` and `acb-agent-edge`. A change stays OWNER-GATE
(`enforcement-flip`).

`PACKAGE_HOST_ORGS` opens `GET /shell/manifest` (`package_host.md` §10). It
also selects the orgs where the daily expiry tick may delete installs
(PKG-10b). A reload that widens it to `*` widens the delete to every org at
the next tick. The owner must treat a change of this list as part of the
expiry decision. `PACKAGE_EXPIRY_ENABLED` does not reload. Only a restart
changes it.

`CHAT_PROGRESS_NOTICE_ORGS` turns on the line of a slow tool
(`chat_run_continuity.md` §4.11, HRD-6). For each listed org, a tool call that
runs past 6 s sends the member one line. A WhatsApp run of a listed org
also gets the long cap of 7 minutes, and its sweep waits 8 minutes
(`bot_run.long_reads_on`). `*` lists every org, and the default is empty. Its
one reader is `acb_skills.progress_notice.progress_notice_on`. The injection
asks it at the start of each run, so a reload applies to the next run. A
reload that takes an org off the list while a long WhatsApp run works gives
that run the short stale time. A flip stays OWNER-GATE (`enforcement-flip`).

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
