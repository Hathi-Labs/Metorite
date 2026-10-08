# The secrets drop

**Status:** BUILT 2026-10-08, for the dev phase. The owner asked for it on
2026-10-08. Fix round 1 landed the same day.
**Script:** `scripts/secrets.sh`. **Manifest:** `deploy/secrets/manifest.json`.
**Fence:** `tests/unit/test_secrets_drop.py`.

The secrets drop puts a secret on the box with one command. You write each
value in a file on your own PC. The script checks the file, makes a backup of
the old file on the box, writes the new file and verifies it. Then it restarts
the units and proves that they stay healthy. It never shows a value.

## 1. The drop folder

The drop folder is `$HOME/.metorite/secrets`. In Git Bash on Windows, that is
`%USERPROFILE%\.metorite\secrets`. It is outside every git checkout. The
script refuses to use a folder inside a checkout.

| File | Entry | What it holds |
|---|---|---|
| `README.txt` | none | What each file is for |
| `backup-gpg-public.asc` | `backup-gpg-public` | The PUBLIC key of the backup |
| `backup-offbox.env` | `backup-offbox` | The seven `BACKUP_S3_*` and `BACKUP_GPG_*` keys |
| `app.env` | `app-env` | Keys for the app env file. The list is empty now |
| `backup-gpg-PRIVATE.asc` | local only | The private backup key. It never goes to the box |
| `backup-gpg-passphrase.txt` | local only | The passphrase of the private key |
| `push.log` | none | One line for each push: time, name, host, hash prefix, result |

Write each env line as `KEY=value`, with no quotes and no spaces. A `#` line
is a comment. A file that a Windows editor saves with CRLF reads the same.

## 2. The commands

Run each command from the root of the repository.

| Command | What it does |
|---|---|
| `scripts/secrets.sh init` | Makes the folder (mode 700), `README.txt` and one template for each env entry. It never writes over a file. |
| `scripts/secrets.sh status [name]` | Shows one state for each entry, with short hash prefixes. |
| `scripts/secrets.sh diff <name>` | Shows the key names that a push adds (`+`), changes (`~`) or removes (`-`). |
| `scripts/secrets.sh push <name> [--yes]` | Checks the file, makes the backup, writes, verifies, restarts and, on a failure, rolls back. |
| `scripts/secrets.sh push --all [--yes]` | Pushes each entry that has its values, in manifest order. |
| `scripts/secrets.sh push ... --verify` | Also runs the post-push check of the entry, when it has one. |
| `scripts/secrets.sh gen backup-gpg [--force]` | Makes the backup key pair with your local gpg. |
| `scripts/secrets.sh forget <key> [--yes]` | Shreds local files, their `.old-*` copies and any leftover `.gnupg-gen.*` folder. |

### The states that `status` shows

| State | Meaning |
|---|---|
| `missing-local` | The local file has no value, and the box has no file. |
| `local-only` | The local file has values, and the box has no file. |
| `in-sync` | The box holds the same values. |
| `differs` | The box holds other values. Run `diff` to see the key names. |
| `remote-only` | The box has the file, and the local file has no value. |
| `unreachable` | The probe failed. The command exits with 1. |

For an env entry, `status` compares a sha256 of each value. It does not
compare comments or the order of the lines. For a `file` entry, it compares a
sha256 of the bytes. The box hashes only the keys in `allowed_keys`. So a
probe of the env file of the app never hashes a value that the manifest does
not manage.

### What `push` does

1. It reads the local file and checks it against the manifest.
2. It refuses a key that is not in `allowed_keys`, so a typo fails.
3. It refuses an empty or missing key from `required_keys`.
4. It refuses a value that fails a validator, or that the shell reads as code.
5. It refuses a key that the file sets two times.
6. On Linux and macOS, it refuses a local file that the group or others can read. On Windows, it shows a warning.
7. It shows the diff. Without `--yes`, it asks you to type `yes`.
8. It sends the new content to the box on ssh STDIN.
9. On the box, it refuses a symbolic link at the target, at its folder or at the backup name.
10. It copies the old file to `<path>.bak-<UTC stamp>`, and keeps the owner and the mode. It keeps the newest 3 backups and deletes the older ones.
11. It writes a temp file in the target folder, sets the owner and the mode, and renames it into place.
12. It compares the hash on the box with the hash of what it sent.
13. It restarts each unit in `restart`, in order. Each unit must pass three checks:
    - `is-active` after 10 seconds.
    - `NRestarts` must not grow after the restart. A unit with `Restart=always` reads active between two crashes, so `is-active` alone proves nothing.
    - The `health` URL of the unit, when it has one, must answer 200.
14. If a unit fails a check, the script puts the backup back and verifies it by hash. Then it restarts the units again. It exits with 2 and the words `ROLLED BACK`.

If the hash check fails, the script restarts nothing. It shows the path of the
backup, and it writes `result=verify-failed` to `push.log`.

### `push --all`

`push --all` goes through the entries in manifest order. It skips an entry
with no local file. It skips an env entry whose template still has an empty
required key, and it names those keys. It skips an entry with no value to
push. It stops at the first entry that fails, and pushes nothing after it.

### The post-push check

An entry can name a `post_push` check. `backup-offbox` has one. It starts
`acb-backup.service` and waits for it to finish. Then it reads the journal of
the unit since the start, and it looks for `off-box copy ok (offbox:`.

The check runs a full backup, so it takes 15 to 25 minutes. It runs only with
`--verify`. Without the flag, the script shows the command to run it. The
check shows the last 5 lines that hold the match, `error`, `warn` or `fail`.
The box replaces each value of the env file with `[REDACTED:<key>]` first. The
PC does the same again with its own values.

### The off-box backup, from zero

Do these steps one time, after a deploy that holds the off-box code (H-123).

```bash
scripts/secrets.sh init
scripts/secrets.sh gen backup-gpg
# Copy backup-gpg-PRIVATE.asc and backup-gpg-passphrase.txt into your password manager.
scripts/secrets.sh forget backup-gpg-private
# Put the S3 key ID and secret in backup-offbox.env. Check BACKUP_S3_REGION in the dashboard.
scripts/secrets.sh push --all --verify
```

`push --all` puts the public key on the box first, then the key file. Then
`--verify` runs one full backup and looks for the off-box line. Then do the
check in H-123.

`gen --force` makes a new key pair. It keeps the old public key as
`backup-gpg-public.asc.old-<stamp>`. It SHREDS the old private key and the
old passphrase. A backup that the old key encrypted needs that private key.
So put it in your password manager first.

## 3. The manifest

`deploy/secrets/manifest.json` holds names, paths and public settings only. It
never holds a value. `.gitignore` keeps every other file in `deploy/secrets/`
out of git. The order of the entries is the order of `push --all`.

| Field | Meaning |
|---|---|
| `name` | The name that you give to `status`, `diff` and `push`. |
| `local_file` | The file name in the drop folder. |
| `kind` | `env-file` writes the whole file. `env-merge` changes only the listed keys in a file that exists, and keeps every other line byte for byte. `file` copies the file as it is. |
| `remote_path` | The absolute path on the box. |
| `owner`, `group`, `mode` | The owner, the group and the octal mode on the box. |
| `allowed_keys` | The only keys that an env file may hold. |
| `required_keys` | The keys that must have a value. |
| `validators` | The checks for each key. A `file` entry puts its checks under `"file"`. |
| `prefill` | Values that `init` writes in the template. Use it for public settings only. |
| `key_notes` | A comment that `init` writes above a key. |
| `restart` | A list of systemd units to restart after the write. Use `[]` for none. |
| `health` | A health URL for a unit in `restart`. It must be `http://127.0.0.1` or `localhost`. |
| `post_push` | `start_unit`, `journal_match`, `timeout_secs` and `about_minutes` of a check that runs with `--verify`. |
| `notes` | Text for the template and `README.txt`. |
| `local_only` | Groups of local files that `forget` can delete. They never go to the box. |

The validators are `non_empty`, `https_url`, `gpg_fingerprint`,
`absolute_path`, `s3_bucket_name` and `gpg_public_key_only`.
`gpg_fingerprint` takes 40 or 64 hex digits, as `backup_offbox.sh` does.
Every env value also gets the rule of `value_problem()` in
`acb_common/env_guard.py`. A test gives the same values to both and fails if
they disagree.

For an `env-merge`, the first line of a managed key gets the new value. A
later line of the same key goes, because the last line wins in systemd and in
bash. A key that the file does not hold goes at the end. A merge never removes
a key.

### How to add an entry

1. Add the entry to `deploy/secrets/manifest.json` in a reviewed change.
2. Use `env-merge` for a file that other tools also write, such as the app env file.
3. Put each key in `allowed_keys`. Put each key that must have a value in `required_keys`.
4. List each unit that loads the file in `restart`. Add a `health` URL where the unit has one.
5. Run `uv run pytest tests/unit/test_secrets_drop.py`.
6. Run `scripts/secrets.sh init`. It adds the new template, and it keeps your other files.

To let the app env take a key, add its name to `allowed_keys` of `app-env`.
⚠️ Never add a `BACKUP_` key to `app-env`. The gateway loads that file (H-270),
and `backup_offbox.sh` refuses it. A test refuses it too.

## 4. The ssh target

The script connects to `metorite`, the ssh alias of the box. Set
`METORITE_SSH_HOST` to use a different host or alias. The user on the box must
have passwordless `sudo`, because the script runs `sudo -n`.

The remote half is a bash script that the command carries in base64. That
script holds no value. Each argument is a path, a name, a mode or a key name
from the manifest.

`plan-guard.mjs` reads the command that an agent types, not the commands that
the script runs. So the guard does not see the ssh of a push, and it does not
guard the drop folder. H-272 holds the three guard arms that close that gap.

## 5. The threat model

**What it stops.**

- A value never goes to stdout, stderr, an argv or `push.log`. A message names the key and never the value.
- A value never goes on argv on the box. It goes on ssh STDIN. `printf` is a bash builtin, so it makes no argv.
- The box gets the value only in a temp file in the target folder, with mode 0600, and in the target and its backups.
- The box sends back hashes of the allowed keys only, and log lines with each value redacted.
- `set +x` runs first, here and on the box, so an inherited xtrace shows no value.
- A symbolic link at the target, its folder or the backup name stops the push. A root write cannot go to a path that a link points to.
- A typo in a key name fails. It does not add a dead line.
- A private key cannot go to the box as the public backup key.
- A unit that does not stay healthy gets the old file back.

The canary test runs every command with one unique value in every secret. It
fails if that value shows in the output, in an argv or in a log. It also fails
if the value is in what the box sends back. And it fails if the value is in a
file on the box other than the target and its backups.

**What it does not stop.**

- The values sit in plain text on the dev PC. Only your user profile protects them. That is acceptable for the dev phase.
- The newest 3 `.bak-<stamp>` files on the box keep old values, with the same owner and mode.
- On an SSD or on NTFS, `shred` cannot promise that the old bytes are gone.
- Anyone who is root on the box, or who is `acb`, can read every value. H-271 holds that risk.
- `plan-guard` does not see the script's own ssh, and it does not guard the drop folder. H-272 holds the fix.

**The follow-up.** Keep the values in the OS keychain, or in files that `age`
encrypts. Then the drop folder holds no plain text.
