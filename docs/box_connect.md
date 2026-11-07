# Connect a headless box with a box token

**Status: BUILT 2026-11-07, dark. Fix round 1 on 2026-11-07.** The routes ship behind the flag `agent_box_tokens_enabled`, which is off. Until an owner turns it on, a box token gets 401. The design is `project-docs/specs/external_agent_access.md` §10.9 (D-EA-5, EA-19).

> ⚠️ **OWNER STEP on a box whose settings enable the project entry.** In a checkout of this repo, `.mcp.json` names `metorite` with no header. When `enabledMcpjsonServers` in the user settings holds `metorite`, that project entry wins over the user entry. Then the box token does NOTHING. Claude Code sends no bearer and starts OAuth, which is the path of the incident.

**An owner must close the project route first. Section 4 names the two ways. The script finds this case and stops with exit code 7.**

**Script:** `scripts/box_connect.sh`. **Fences:** `tests/unit/test_box_token_secret_rules.py` and `tests/unit/test_box_token_claude_client.py`.

## Why a box token

On 2026-11-06 at 01:23 UTC, one session on `metorite-home` presented a spent refresh token. The server then revoked the connection for all nine sessions on the box. A box token has no refresh token, so no session on the box can sign out the others.

## What a box token is

- A box token is `mbt_` and 43 URL-safe characters. The `mbt_` prefix lets gitleaks and the log redactor find it.
- It acts as the member who made it, with the scopes that the member chose.
- It lives 90 days by default, and at most 365. Use does not extend it.
- Claude Code sends it as a static `Authorization` header. Claude Code does not start OAuth when the server refuses it.

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
- It writes the user-scope MCP entry `metorite`. The header is `Bearer ${METORITE_BOX_TOKEN}`, so the token is not in `.claude.json`.
- In the project folder, it runs `claude mcp get metorite`. The `Scope:` line must say `User config`, and the header must show `Bearer ${METORITE_BOX_TOKEN}`. A `Project config` or `Local config` scope stops it with exit code 7, and it does not show `Connected`.
- Then it runs `claude mcp list` in the same folder, and shows the result. Each command has 60 seconds.

The script never shows the token. If a user-scope `metorite` entry exists, the script refuses and writes nothing. It also refuses for a local-scope entry of the project folder, because that entry wins over the user entry. To replace the entry, add `--replace`.

For an old entry, the script shows the type, the address and the kind of header, and never a header value. A token in the address shows as `mbt_[redacted]`. The new write can fail after the script removed the old entry. Then the script shows the old address and the kind of header again, so that you can restore the entry.

| Exit code | Meaning |
|---|---|
| 0 | Connected, or written with `--no-verify` |
| 2 | A bad argument |
| 3 | The token is empty or has the wrong shape |
| 4 | `claude` or `python3` is not on the path |
| 5 | A `metorite` entry exists, and `--replace` is not set |
| 6 | A write failed |
| 7 | In the project folder, the effective `metorite` entry is not the user entry with the box token header, or `claude mcp list` does not show it as connected |

## 3. Load the token in each lane shell

Each shell that starts `claude` must load the token first:

```
set -a; . ~/.config/metorite/box.env; set +a
```

Put this line in the start script of each lane. Without it, Claude Code sends the text `Bearer ${METORITE_BOX_TOKEN}` as it is, and the server refuses it. `claude mcp list` then shows the warning "Missing environment variables: METORITE_BOX_TOKEN".

## 4. Verify

Do these steps in the folder where the lanes start `claude`, in a shell that loaded `box.env`.

1. Run `claude mcp get metorite`. The line `Scope:` must say `User config`. The header must show `Bearer ${METORITE_BOX_TOKEN}`, and not the token.
2. Run `claude mcp list`. The line for `metorite` must say `Connected`.

### The project entry of `.mcp.json` (OWNER STEP)

The repo file `.mcp.json` also names `metorite`, with the same address and no header. Claude Code 2.1.319 picks one entry in this order: a local-scope entry, then an approved project entry, then the user entry. A project entry is approved when `enabledMcpjsonServers` holds its name. On `metorite-home`, `~/.claude/settings.json` holds `enabledMcpjsonServers: [metorite, supabase, codegraph]`. So in a checkout on that box, the project entry wins, and the box token does nothing.

`claude mcp get metorite` then says `Scope: Project config`, and the script stops with exit code 7. An owner must close the project route. There are two ways:

1. In the user settings, move `metorite` from `enabledMcpjsonServers` to `disabledMcpjsonServers`. Other files can also approve it, for example `.claude/settings.local.json` of the project. Look in each.
2. Change the repo's `.mcp.json`, so that it does not name `metorite` with no header. This changes every checkout, so it is an owner decision (`project-docs/specs/external_agent_access.md` §17, task 1842).

The script does not edit a settings file or `.mcp.json`. A local-scope entry wins the same way. Remove it with `claude mcp remove -s local metorite` in that folder, or run the script again with `--replace`.

## 5. Rotate

1. In **Integrations**, **AI agents**, select **Rotate** on the box row. Copy the new token.
2. On the box, run `bash scripts/box_connect.sh --replace`, and paste the new token.
3. Restart each lane, so that each shell loads the new `box.env`.

**Rotate** keeps the old token for 24 hours. **Rotate now** stops the old token at once. Use **Rotate now** when the row shows the new-address mark.

## 6. Revoke

1. In **Integrations**, **AI agents**, select **Revoke** on the box row.
2. On the box, delete `~/.config/metorite/box.env`.
3. Remove the entry with `claude mcp remove -s user metorite`.

The revoke takes effect on the next call.

## What a revoked token looks like

`claude mcp list` shows this line:

```
metorite: https://mcp.metorite.com/mcp (HTTP) - ✘ Failed to connect — Server rejected the configured Authorization header (HTTP 401) ... OAuth fallback is disabled when headers.Authorization is set
```

The script then stops with exit code 7. This is the correct result. No sign-in loop starts. Make a new token, then run the script again with `--replace`.

## One stamp for all lanes

All lanes on one box use one token, so they share one stamp, `mcp:box:<name>-<8 hex>`. When `agent_mcp_sessions_enabled` (SW-7) is on, each Claude Code process still gets its own session key. So `my_work` still tells the lanes apart. For a trail for each lane, make one token for each lane.

## Residual risks

- Each process that inherits the env var can read the token. Root can read it too.
- A box token is a bearer secret. It has no proof of possession, so a thief with the token can use it from another address.
- The new-address mark compares the address at most once a minute. Calls between two compares are not seen.
- A script with a stolen session cookie can mint a token. The mint ticket and the same-origin check do not stop that thief.
- The log redactor covers the logs that go through `acb_common.configure_logging`. It walks the event, bytes and sets too, and it also masks the rendered line. A log that a service writes through its own handler does not get the mask.
- `acb-agent-edge` does not call `configure_logging`, so the redactor does not mask the edge logs. The uvicorn loggers do not reach the root handler either. Today the bearer check of the edge logs only the type name of an exception and the connection id, and no header. A new log line at the edge must keep to that.
