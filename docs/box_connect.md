# Connect a headless box with a box token

**Status: BUILT 2026-11-07, dark. Fix round 1 on 2026-11-07. The header helper (EA-19g, task 1819) BUILT 2026-11-26.** The routes ship behind the flag `agent_box_tokens_enabled`, which is off. Until an owner turns it on, a box token gets 401. The design is `project-docs/specs/external_agent_access.md` §10.9 (D-EA-5, EA-19).

> ⚠️ **OWNER STEP on a box whose settings enable the project entry.** In a checkout of this repo, `.mcp.json` names `metorite` with no header. When `enabledMcpjsonServers` in the user settings holds `metorite`, that project entry wins over the user entry. Then the box token does NOTHING. Claude Code sends no bearer and starts OAuth, which is the path of the incident.

**An owner must close the project route first. Section 4 names the two ways. The script finds this case and stops with exit code 7.**

**The org switch (EA-19h, task 4034) BUILT 2026-11-26.** Section 7 holds the steps.

**Scripts:** `scripts/box_connect.sh`, the header helper `scripts/metorite_box_headers.sh` and the org switch `scripts/metorite_switch_org.sh`. **Fences:** `tests/unit/test_box_token_secret_rules.py`, `tests/unit/test_box_token_claude_client.py` and `tests/unit/test_metorite_switch_org.py`.

## Why a box token

On 2026-11-06 at 01:23 UTC, one session on `metorite-home` presented a spent refresh token. The server then revoked the connection for all nine sessions on the box. A box token has no refresh token, so no session on the box can sign out the others.

## What a box token is

- A box token is `mbt_` and 43 URL-safe characters. The `mbt_` prefix lets gitleaks and the log redactor find it.
- It acts as the member who made it, with the scopes that the member chose.
- It lives 90 days by default, and at most 365. Use does not extend it.
- Claude Code gets the `Authorization` header from a header helper, and sends it. Claude Code does not start OAuth when the server refuses it.

## 1. Make the token

1. In Metorite, open **Integrations**, then **AI agents**.
2. Select **Connect a box**. Give the box a name, choose the scopes and the days.
3. Copy the token. Metorite shows it one time only.
4. Do not paste the token into a chat, a ticket, a commit or a shell command line.

## 2. Run the command on the box

1. Sign in to the box as the user who runs the agent lanes.
2. In a checkout of this repo, run this command:

   ```
   bash scripts/box_connect.sh
   ```

3. Paste the token at the prompt. The prompt does not show what you paste.
4. To give the token from a pipe, send it on stdin. Never give it as an argument, because `ps` shows the arguments.
5. Do not type `echo mbt_... | bash scripts/box_connect.sh`. The shell history keeps that line, and the token with it. Paste at the hidden prompt, or redirect stdin from a file of mode 0600 that you delete after.
6. If the lanes start `claude` in another folder, give that folder with `--project DIR`. The default is the current folder when its `.mcp.json` names `metorite`. Without one, the script checks from your home folder.

The script does these steps:

- It checks the shape of the token before it writes a file. A bad shape stops it with exit code 3.
- It writes `~/.config/metorite/box.env` with mode 0600. The file holds one line, `METORITE_BOX_TOKEN=<token>`.
- It installs the header helper as `~/.local/bin/metorite-box-headers`, with mode 0700. This path is outside the checkout, so a worktree removal does not break the lanes.
- It writes the user-scope MCP entry `metorite`. The entry has `headersHelper` and no `headers`. The helper command is the helper path and the `box.env` path. The token is not in `.claude.json`.
- In the project folder, it runs `claude mcp get metorite`. The `Scope:` line must say `User config`. A `Project config` or `Local config` scope stops it with exit code 7, and it does not show `Connected`.
- `claude mcp get` does not show a `headersHelper`. So the script reads the entry from `.claude.json`. The entry must name the helper and hold no `headers`. Then the script runs the helper once, and discards what it prints.
- Then it runs `claude mcp list` in the same folder, and shows the result. Each command has 60 seconds. `claude` gets no `METORITE_BOX_TOKEN` in its environment.

The script never shows the token. If a user-scope `metorite` entry exists, the script refuses and writes nothing. It also refuses for a local-scope entry of the project folder, because that entry wins over the user entry. To replace the entry, add `--replace`.

For an old entry, the script shows the type, the address and the kind of header, and never a header value. A token in the address shows as `mbt_[redacted]`. The new write can fail after the script removed the old entry. Then the script shows the old address and the kind of header again, so that you can restore the entry.

| Exit code | Meaning |
|---|---|
| 0 | Connected, or written with `--no-verify` |
| 2 | A bad argument, or a `HOME` path with a character that the helper command cannot hold |
| 3 | The token is empty or has the wrong shape |
| 4 | `claude` or `python3` is not on the path, or the checkout has no header helper |
| 5 | A `metorite` entry exists, and `--replace` is not set |
| 6 | A write failed |
| 7 | In the project folder, the effective `metorite` entry is not the user entry with the header helper, the helper refuses `box.env`, or `claude mcp list` does not show it as connected |

## 3. A lane does not load the token

**Not needed since 2026-11-26 (task 1819).** A lane shell does not load `box.env`. Claude Code runs the header helper each time it connects the entry, when it connects again, and after a 401 or 403. The helper reads `box.env` and prints `{"Authorization":"Bearer <token>"}`.

The old entry used the header `Bearer ${METORITE_BOX_TOKEN}`. Claude Code expands that variable from its own environment. On the box, a long-lived `claude daemon` starts each `claude --bg` lane, and the daemon never loaded `box.env`. So the old header could go out with no token. To move a box from the old entry to the helper, run the script again with `--replace`.

The helper refuses `box.env` when the file does not exist, is a symlink, has a second hard link, is not mode 0600, or is not owned by you. It also refuses a token of the wrong shape. A refusal shows one line on stderr and never the token.

A refusal still prints an `Authorization` header with the bearer `box-token-helper-refused`, and exits 0. The server answers 401 to it, and Claude Code then starts no OAuth. Without that header, Claude Code starts OAuth discovery and registers a client (review of 2026-11-26). So a refused `box.env` shows as "Failed to connect", never as a sign-in prompt.

The session-start hook `.claude/hooks/session-handoff.mjs` also runs the helper when `METORITE_BOX_TOKEN` is not set. It reads the refusal bearer as no credential. To test the helper by hand, run `~/.local/bin/metorite-box-headers >/dev/null`. No line on stderr means that it reads the token. The exit code is 0 in both cases.

The helper takes the path of `box.env` from the entry only. No environment variable changes the path, because a project settings file can set a variable.

## 4. Verify

Do these steps in the folder where the lanes start `claude`. You do not load `box.env` first.

1. Run `claude mcp get metorite`. The line `Scope:` must say `User config`.
2. Read the user entry in `~/.claude.json`, under `mcpServers.metorite`. It must hold `headersHelper` with the helper path and the `box.env` path, and no `headers`. `claude mcp get` does not show the helper.
3. Run `claude mcp list`. The line for `metorite` must say `Connected`.

### The project entry of `.mcp.json` (OWNER STEP)

The repo file `.mcp.json` also names `metorite`, with the same address and no header. Claude Code 2.1.319 picks one entry in this order: a local-scope entry, then an approved project entry, then the user entry. A project entry is approved when `enabledMcpjsonServers` holds its name. On `metorite-home`, `~/.claude/settings.json` holds `enabledMcpjsonServers: [metorite, supabase, codegraph]`. So in a checkout on that box, the project entry wins, and the box token does nothing.

`claude mcp get metorite` then says `Scope: Project config`, and the script stops with exit code 7. An owner must close the project route. There are two ways:

1. In the user settings, move `metorite` from `enabledMcpjsonServers` to `disabledMcpjsonServers`. Other files can also approve it, for example `.claude/settings.local.json` of the project. Look in each.
2. Change the repo's `.mcp.json`, so that it does not name `metorite` with no header. This changes every checkout, so it is an owner decision (`project-docs/specs/external_agent_access.md` §17, task 1842).

The script does not edit a settings file or `.mcp.json`. A local-scope entry wins the same way. Remove it with `claude mcp remove -s local metorite` in that folder, or run the script again with `--replace`.

## 5. Rotate

1. In **Integrations**, **AI agents**, select **Rotate** on the box row. Copy the new token.
2. On the box, run `bash scripts/box_connect.sh --replace`, and paste the new token.
3. The Claude Code docs say that Claude Code runs the helper again after a 401. So a lane reads the new `box.env` with no restart. No test in this repo proves that path. If a lane shows a failed `metorite` call, restart that lane.

**Rotate** keeps the old token for 24 hours. **Rotate now** stops the old token at once. Use **Rotate now** when the row shows the new-address mark.

## 6. Revoke

1. On the box, remove the entry first: `claude mcp remove -s user metorite`.
2. In **Integrations**, **AI agents**, select **Revoke** on the box row.
3. On the box, delete `~/.config/metorite/box.env` and `~/.local/bin/metorite-box-headers`.

The revoke takes effect on the next call.

## 7. Switch the organization of a machine

A machine stays signed in. The only act that ends its sign-in is an org switch (owner directive, 2026-11-26). The script `scripts/metorite_switch_org.sh` does the switch, and it logs each one.

The user entry runs the header helper on each connect. So a switch only puts a new token into `box.env`, and then the sessions start again. The script runs no `claude mcp` command.

> ⚠️ **Run the switch from a plain shell, never from a Claude session.** With `--stop-lanes` it stops each live session, and a session that runs it stops itself. The script refuses when `CLAUDECODE` or a `CLAUDE_CODE_*` variable is set.

Keep one token for each organization:

1. Make a box token in each organization (§1).
2. For each organization, run `bash scripts/metorite_switch_org.sh add <label>`, and paste the token at the hidden prompt.
3. A label is 1 to 32 characters of `a-z`, `0-9` and `-`. It starts with a letter or a digit.
4. The script writes `~/.config/metorite/orgs/<label>.env`, mode 0600. To replace a file, add `--replace`.
5. To see the labels, run `bash scripts/metorite_switch_org.sh list`. A `*` marks the active one. It never shows a token.

Switch the machine:

1. Run `bash scripts/metorite_switch_org.sh to <label> --dry-run`. It shows the live sessions and changes nothing.
2. Run `bash scripts/metorite_switch_org.sh to <label> --stop-lanes`.
3. Start each stopped lane again as a fresh session, not as a resume (`docs/home_box.md` §3.3). The script prints the names.

The switch does these steps:

- It refuses a label with no org file. It runs the helper on the org file, and refuses when the helper refuses it.
- It refuses when the user entry does not name the helper and `box.env`. Then run `scripts/box_connect.sh` first.
- It counts the live sessions with `claude agents --json`. A session is live when it has a process. It refuses while a session is live, unless you add `--stop-lanes`. Then it runs `claude stop <id>` for each one.
- It copies `box.env` to `box.env.prev`, and writes the new token into `box.env`. Both files have mode 0600.
- It calls `initialize` and then `whoami` at the address of the entry, with the header of the helper. It shows the email and the organization id.
- If `whoami` fails, it puts `box.env.prev` back, and the active org does not change.
- It writes the label into `~/.config/metorite/active-org`. It adds one line to `~/.local/state/metorite/org-switch.log`. When `~/lanes/merge-queue.log` exists, it adds one `NOTE` line there too.

The lane keeper can wake an ended lane during the switch. That lane reads `box.env` when it connects, so it gets the new token after the write.

| Exit code | Meaning |
|---|---|
| 0 | Switched, or `--dry-run` done |
| 2 | A bad argument or a bad label |
| 3 | `add`: the token is empty or has the wrong shape |
| 4 | `python3` or `claude` is not on the path, or the checkout has no header helper |
| 5 | `add`: the org file exists, and `--replace` is not set |
| 6 | A write failed |
| 7 | The user entry does not name the header helper, or the helper is not installed |
| 8 | The label has no org file, or the helper refuses the org file |
| 9 | A session is live and `--stop-lanes` is not set, the shell is a Claude session, or a read or a stop of the sessions failed |
| 10 | `whoami` failed with the new token. The script put `box.env` back |

The script never shows, logs or passes a token in a command line.

## What a revoked token looks like

`claude mcp list` shows this line:

```
metorite: https://mcp.metorite.com/mcp (HTTP) - ✘ Failed to connect — Server rejected the Authorization header minted by the configured headersHelper (HTTP 401) ... OAuth fallback is disabled when the helper supplies Authorization
```

The script then stops with exit code 7. This is the correct result. No sign-in loop starts. Make a new token, then run the script again with `--replace`.

## One stamp for all lanes

All lanes on one box use one token, so they share one stamp, `mcp:box:<name>-<8 hex>`. When `agent_mcp_sessions_enabled` (SW-7) is on, each Claude Code process still gets its own session key. So `my_work` still tells the lanes apart. For a trail for each lane, make one token for each lane.

## Residual risks

- Each process of the box user can read `box.env`. Root can read it too. The token is not in the environment of a lane.
- A box token is a bearer secret. It has no proof of possession, so a thief with the token can use it from another address.
- The new-address mark compares the address at most once a minute. Calls between two compares are not seen.
- A script with a stolen session cookie can mint a token. The mint ticket and the same-origin check do not stop that thief.
- The log redactor covers the logs that go through `acb_common.configure_logging`. It walks the event, bytes and sets too, and it also masks the rendered line. A log that a service writes through its own handler does not get the mask.
- `acb-agent-edge` does not call `configure_logging`, so the redactor does not mask the edge logs. The uvicorn loggers do not reach the root handler either. Today the bearer check of the edge logs only the type name of an exception and the connection id, and no header. A new log line at the edge must keep to that.
