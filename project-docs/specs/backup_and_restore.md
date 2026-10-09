# Backup & Restore — BO-23

**Status:** The scripts SHIPPED. **Scheduling CLOSED on 2026-08-05.** A real
run proved the timer. **The off-box copy is BUILT (2026-10-08).** It stays
OFF until the owner does the four steps in §4.2 (H-123).

**2026-10-08, owner decision:** the off-box copy goes to a private Supabase
Storage bucket, through its S3 endpoint. The box encrypts each night to the
owner's PUBLIC key before the upload. Only the nightly unit uploads
(`--offbox`), and the bucket key is in a root-owned file. Branch
`ops-offbox-backup`. A real S3 server (MinIO) proved the round trip in
`scripts/rehearse_offbox.sh`. Nobody has measured it on the box yet.

**2026-10-07, after an I/O incident on the managed cluster:** `--verify-restore`
now restores into a throwaway container on the box, and never into the live
cluster. The pre-migration backup runs only when the ledger shows a migration
to apply.
Branch `ops-backup-io`. The verify on the box is not yet measured.

**Owner row:** FOUNDATION_BUILDOUT_CHECKLIST.md §BO-23
**Last measured:** 2026-08-05 against the live VPS (srv1747539); §1's recovery
position was measured 2026-08-03 and is unchanged

---

## 1. What the recovery position actually was

Measured, not assumed — via the Hostinger API on 2026-08-03:

| | |
|---|---|
| Application-level backup | **none** — no dump, no cron, no script anywhere in the repo or on the box |
| Hostinger VM images | **2 retained**: `2026-07-22`, `2026-07-29` |
| Cadence | weekly |
| Age of newest image when measured | **5 days** |
| Worst-case RPO | **~7 days of data loss** |
| Restore time | `restore_time: 3457` ≈ **58 minutes** |
| Granularity | the **whole machine** — code, `.env`, Docker volumes, everything |
| VPS snapshot | **none** (`id: 0`, `created_at == expires_at`) |

Two consequences that matter more than the numbers:

1. **"Recover one dropped table" was not a thing this system could do.** The
   only lever reverted the entire VPS to a point up to a week earlier, taking
   the code and every other database with it.
2. **140+ migrations replay forward-only on every deploy** (`scripts/apply_migrations.sh`).
   A migration that corrupts data had no granular undo.

The databases are small — `acb` 193 MB, `litellm_proxy` 11 MB, 67 GB free — so
nightly full logical dumps are cheap. There was no cost reason for the gap.

## 2. What shipped

| File | Role |
|---|---|
| `scripts/backup_db.sh` | dump every non-template DB + globals, integrity-check, manifest, retention, optional off-box copy |
| `scripts/restore_db.sh` | restore — **to a scratch DB by default**, live only behind `--force` |
| `scripts/offbox_lib.sh` | the off-box layout, the rclone settings and the one delete guard (H-123) |
| `scripts/restore_offbox.sh` | list, download, decrypt and verify one night of the off-box copy |
| `scripts/rehearse_offbox.sh` | the real round trip: Postgres, gpg, zstd, rclone and an S3 server |

Both talk to Postgres through `docker exec acb-postgres`, so `pg_dump` is
always the same major version as the server. Credentials are read from
`/opt/acb/app/.env` exactly as `apply_migrations.sh` reads them, so the two can
never disagree about which cluster is "the" database.

### Design decisions worth not re-litigating

- **`-Fc` (custom format), not plain SQL.** A plain dump can only be replayed
  whole. Custom format lets `pg_restore --table gtd_task` pull one table out,
  which is the case that motivated the ticket.
- **Globals are dumped separately** (`pg_dumpall --globals-only`). Roles and
  passwords live in the cluster, not in any database. A per-database dump
  restored into a fresh cluster comes up with no roles and every `GRANT` in it
  fails — so a bare-metal rebuild would have been impossible from database
  dumps alone.
- **Databases are enumerated, not hardcoded.** `litellm_proxy` holds API keys
  and spend records and would have been silently missed by an `acb`-only backup.
- **Backups live in `/opt/acb/backups`, deliberately NOT under `/opt/acb/app`.**
  The deploy runs `git reset --hard`, which has already destroyed tracked
  runtime state in this repo's history. Anything under the app dir is one
  deploy away from being gone.
- **Every run integrity-checks the dump** with `pg_restore --list`. A truncated
  or half-written file fails at backup time rather than during an incident.
  This is the difference between having a backup and believing you have one.
- **`--verify-restore` proves a different claim.** The cheap check proves the
  file is *readable*. The deep check restores it and compares the `public`
  table count with live, which proves it is *restorable*.
  **Since 2026-10-07, the restore goes into a throwaway container on the box.**
  The image is `pgvector/pgvector:pg<live major>`, with no network and a 1g
  memory cap. The verify restores `public` only, and any error fails it. The
  old restore into the managed cluster used up its disk I/O budget. Without
  Docker the verify fails, and it never falls back to the cluster. A failed
  verify still lets the Console dump, the off-box copy and retention run.
  Then the run exits 1.
  ~20s on a 193 MB database.
- **Restore defaults to safe.** With no flags it builds `acb_restored_<ts>` and
  touches nothing live. Overwriting live needs `--target acb --force`, takes an
  unconditional pre-restore dump first, and stops the gateway so users are not
  served half-restored state.

## 3. Runbook

```bash
# what have we got?
scripts/restore_db.sh --list

# take one now
scripts/backup_db.sh --verify-restore

# incident: inspect last night's data without touching production
scripts/restore_db.sh
docker exec -it acb-postgres psql -U acb -d acb_restored_<ts>

# recover a single table
scripts/restore_db.sh --table gtd_task

# full rollback — DESTRUCTIVE, takes a safety dump first, stops the app
scripts/restore_db.sh --target acb --force
```

## 3a. 🔴 Merge precondition — read before landing the PR

`scripts/apply_migrations.sh` now **fails closed** without a pre-migration
dump, and that runner is on the release path. **`backup_db.sh` has never been
executed** — no agent was permitted to run it against the production database.

So the fail-closed gate is, until proven, an untested script that can break
every release. Run it by hand **once** before merging:

```bash
ssh <box> 'bash /opt/acb/app/scripts/backup_db.sh --verify-restore'
```

Expect `restore verified` and `public tables: live=N restored=N`. If it fails,
either fix it or land the PR with `SKIP_PRE_MIGRATION_BACKUP=1` set in the
release environment until it passes — do not merge and hope.

The script's own failure modes are bounded (it writes to a fresh timestamped
directory, and exits non-zero before touching anything), so a failed run costs
a directory, not data.

## 4. Still open — OWNER-GATE

These could not be completed by an agent. Two independent guards refused:
`plan-guard` blocks writes under `deploy/`, and the runtime classifier blocks
live VPS configuration changes. Both refusals were correct.

### 4.1 Schedule it — ✅ CLOSED 2026-08-05

The two units were written **directly to `/etc/systemd/system/`** from §5's
contents, not installed from `deploy/hostinger/` — those repo files were never
committed, so the `install` command below pointed at nothing. The timer is
enabled and nightly at 02:30 UTC with `Persistent=true`.

```bash
# The units are in §5. Write them to /etc/systemd/system/ directly.
sudo systemctl daemon-reload
sudo systemctl enable --now acb-backup.timer
sudo systemctl start acb-backup.service   # prove it works now, do not wait for 02:30
sudo systemctl status acb-backup.service
```

⚠️ **That last line is not ceremony, and this is the durable lesson from BO-23.**
The first real run **failed**, on a bug nothing else would have surfaced:

```
/opt/acb/app/scripts/backup_db.sh: line 142: /tmp/verify_restore.log: Permission denied
!! pg_restore FAILED — acb-backup.service: Failed with result 'exit-code'
```

`--verify-restore` wrote pg_restore output to `/tmp/verify_restore.log`. Ubuntu
sets `fs.protected_regular=2`, which forbids opening an existing file in a sticky
world-writable directory owned by a **different** user — and unlike ordinary
permissions, **root is not exempt**. An earlier hand-run as `acb` created the
file; every root-run unit invocation after that hit EACCES. The dump itself was
always fine, so the failure mode was the worst available shape: a nightly unit
reporting FAILURE while the data it produced was perfectly good — discovered
during an incident, when the operator most needs to trust the backup.

Fixed in PR #359 (`$DEST/verify_restore.log`, beside the dump it describes). **A
timer that has never been fired is not a schedule; it is a plan.**

Verified after the fix:

```
Result=success   ExecMainStatus=0
public tables: live=228 restored=228
restore verified
4 backup(s) retained, 90M total
```

### 4.2 Off-box copy — Supabase Storage, BUILT 2026-10-08 (H-123)

> **DECISION (owner, 2026-10-08):** off-box backups go to **Supabase Storage**,
> through its S3-compatible endpoint, in a **private** bucket. This replaces the
> deferral of 2026-08-05. Beta customers start to upload files soon, and those
> files must not exist in one place only.

#### What the nightly unit sends

Only `acb-backup.service` passes `--offbox` to `backup_db.sh`. With that flag
and the keys of `/etc/acb/backup-offbox.env`, `backup_offbox.sh` sends the
night to `<bucket>/<prefix>/<stamp>/`. The default prefix is `nightly`, and
`<stamp>` is the name of the local backup folder. Each object is
`<name>.zst.gpg`:

| Object | What it holds |
|---|---|
| `acb.dump`, other `*.dump` | every database dump of the run, the Console dump too |
| `globals.sql`, `MANIFEST.txt` | the roles, and the manifest with its checksums |
| `files.tar` | Tasks and Projects attachments, meeting audio, agent workspaces (`BACKUP_FILE_DIRS`) |
| `meeting-bot-volume.tar` | the meeting bot's Docker volume, read in place while the bot runs |
| `SHA256SUMS` | the checksum of each plain item. It goes up LAST, so a night without it is incomplete |

The run skips a directory or a volume that does not exist, and it writes one
line for each.

#### How it is safe

- **Opt-in, in one place.** Without `--offbox`, the run uploads nothing, whatever
  the env holds. The pre-migration backup of a deploy never passes the flag.
  So a deploy never uploads, and a bucket outage cannot block a migration.
  `test_only_the_nightly_unit_passes_offbox` checks every caller.
- **The key is in a root-owned file.** A Supabase S3 key is
  **project-wide**. It passes Row Level Security, and it can read or delete
  every object in every bucket of the project. So every `BACKUP_S3_*` and
  `BACKUP_GPG_*` key lives in `/etc/acb/backup-offbox.env`, root:root 0600.
  Only `acb-backup.service` loads that file. The trade-offs below say what
  this file does NOT stop.
  ⚠️ **The project-wide key is RETIRED (2026-10-08).** `box_hardening.md`
  BH-4 owns the new credential: a Supabase user JWT that RLS limits to
  INSERT and SELECT on this bucket, with retention on the Supabase side.
  Never make the project-wide key. If one exists, revoke it.
- **Never in the app env file.** `/opt/acb/app/.env` is the env file of
  `acb-gateway` and of the WhatsApp bridge. The gateway's in-process Copilot
  CLI inherits that env (H-270). `backup_offbox.sh` refuses to run as any
  user but root. It also refuses a key file that is not root:root 0600. The
  scripts read every off-box name from the key file only, and ignore the same
  names in the env (BH-6a, 2026-10-09). A `BACKUP_S3_*`, `BACKUP_GPG_*` or
  `BACKUP_OFFBOX_ENV_FILE` line in an acb-writable `.env` makes the run exit 1
  with an ERROR. The night still goes up with the values of the key file.
- **Encrypted on the box, or not sent.** zstd compresses each item, and gpg
  encrypts it to the PUBLIC key named by `BACKUP_GPG_RECIPIENT` (a full
  fingerprint). The box never holds the private key. So the box can write a
  backup, and it cannot read one.
- **The script checks the key before any data moves.** A missing, unreadable,
  private, revoked or wrong key fails the step, and rclone never runs. A
  failed encryption of one file also stops the step before the upload.
- **No secret on disk or on argv.** rclone reads the bucket key from its
  environment only, as `RCLONE_CONFIG_OFFBOX_*`. `RCLONE_CONFIG=/dev/null`
  stops rclone from reading a config file. The script first clears every
  inherited `RCLONE_*` name. `env_guard` refuses a tenant write to
  `BACKUP_*`, `RCLONE_*`, `ZSTD_*`, `TAR_OPTIONS` and `GNUPGHOME`.
- **One delete, with one guard.** `offbox_delete_night` is the only delete.
  It accepts a night stamp only, and it builds the path from the checked
  bucket and prefix. So no delete can reach a path outside
  `<bucket>/<prefix>/<stamp>`.
- **Retention is by UTC day, and counts complete nights only.** A night is
  complete when it holds `SHA256SUMS.zst.gpg`. Retention keeps the newest
  complete night of each UTC day, for the newest `BACKUP_S3_KEEP` days that
  have one (default 14, read in base 10). Many runs in one day use one slot,
  so they cannot push the earlier days out. A day with no complete night uses
  no slot, so a gap in the backups deletes nothing.
- **Incomplete nights.** An incomplete night goes only when it is older than
  the oldest kept night. The newest incomplete night never goes, because its
  upload may not be done yet. Retention runs only after a good upload.
- **Bounded by the time left in the unit.** Local retention runs first. The
  off-box deadline is `min(BACKUP_S3_TIMEOUT_SECS, 1800 - elapsed - 60)`, in
  seconds, and `timeout` adds 30 s before a KILL. The default for
  `BACKUP_S3_TIMEOUT_SECS` is 1200. 1800 is the unit's `TimeoutStartSec`,
  and `backup_db.sh` names it `unit_timeout_secs`. A test fails when the two
  values drift.
- **Too little time is a failure.** With under 120 s left, the upload does
  not start, and the run records a failure. A timeout is a failed upload too.
  The recorded pre-migration dump took about 11 minutes, so a fixed deadline
  could outlive the unit.
- **A failure costs no local backup.** Any failure is an ERROR, and the unit
  exits 1 at the end. The local dump, the Console dump and local retention
  run first. A failed Console dump does not stop the off-box copy of the app
  dump.

The fences are in `tests/unit/test_backup_deploy_wiring.py`. The real round
trip is `scripts/rehearse_offbox.sh` (§6.2).

#### The trade-offs, recorded

- **One account.** The bucket is in the **same Supabase account** as the
  database. It protects against the loss of the VPS, which is the gap that
  H-123 names. It does not protect against the loss of the Supabase account.
- **The root-owned file stops the PASSIVE paths only.** The app does not
  inherit the key, and `/proc/<gateway pid>/environ` does not show it. An env
  dump in a log or a crash report does not hold it either.
- **An ACTIVE compromise of the app can still read the key (H-271).** On this
  box `acb` has the same power as root, in three ways. It has passwordless sudo.
  It is in the `docker` group, so it can mount `/etc/acb` into a container.
  It owns the checkout, and the root unit runs `backup_db.sh` and
  `backup_offbox.sh` from that checkout. So an attacker with acb, or with the
  app, can read the key and delete every off-box night.
- **What the copy protects against.** It protects against the loss of the
  VPS, the disk or the provider account. It does NOT protect against a
  compromise of the app. The real fixes are owner decisions, and H-271
  lists them. A write-only bucket credential with retention on the server
  side, or a second provider with Object Lock, closes the gap.
- **Integrity, not authenticity (F7).** The box encrypts each night, and it does
  not sign it, because the box holds no signing key. `SHA256SUMS` proves that a
  night is whole. It does not prove who wrote it. A person with the bucket key
  and the public key can write a whole, false night. So restore only from a
  night whose date and size you expect.
- **Names and sizes are plain (F10).** The bucket shows the name of each item
  (for example `acb.dump`), the night stamp and the size of each object. It
  does not show the content.

#### The owner steps to switch it on

Do these after a deploy of this branch, so that rclone is on the box and the
unit has `--offbox`.

1. **Make the private bucket** `metorite-backups` in project
   `wbjpwtxigkileyjsgahk`. The coordinator may run this SQL for you:

   ```sql
   insert into storage.buckets (id, name, public)
   values ('metorite-backups', 'metorite-backups', false)
   on conflict (id) do nothing;
   ```

   ⚠️ The app dump is about 84 MB, and one object holds it whole. Open
   Storage → Settings and make sure the upload size limit is 1 GB or more.
   The Free plan stops at 50 MB.
2. **RETIRED (2026-10-08).** This step made a project-wide S3 access key.
   Do NOT make one. That key can read and delete every object in every
   bucket of the project. If one exists, revoke it at Storage → S3
   Connection. `box_hardening.md` BH-4 owns the write-only credential that
   takes its place, and its key list for step 4.
3. **Make a gpg key pair on your own machine.** Keep the private key in your
   password manager, and put only the public key on the box.

   ```bash
   gpg --quick-gen-key "Metorite backups <you@example.com>" ed25519 cert never
   FPR="$(gpg --with-colons --list-keys "Metorite backups" | awk -F: '/^fpr:/ {print $10; exit}')"
   gpg --quick-add-key "$FPR" cv25519 encr never
   gpg --armor --export "$FPR" > metorite-backups-public.asc
   gpg --armor --export-secret-keys "$FPR" > metorite-backups-PRIVATE.asc
   echo "$FPR"
   # Put metorite-backups-PRIVATE.asc and its passphrase in the password
   # manager. Then delete the file from this machine.
   scp metorite-backups-public.asc metorite:/tmp/
   ssh metorite 'sudo install -m 0644 /tmp/metorite-backups-public.asc /opt/acb/backup-public-key.asc'
   ```

4. **Put the keys in the root-owned file.** Do NOT put them in
   `/opt/acb/app/.env`. The run refuses that.

   ```bash
   ssh metorite
   sudo mkdir -p /etc/acb
   sudo touch /etc/acb/backup-offbox.env
   sudo chown root:root /etc/acb/backup-offbox.env
   sudo chmod 0600 /etc/acb/backup-offbox.env
   sudoedit /etc/acb/backup-offbox.env
   ```

   Put these seven lines in the file:

   ```bash
   BACKUP_S3_ENDPOINT=https://wbjpwtxigkileyjsgahk.storage.supabase.co/storage/v1/s3
   BACKUP_S3_REGION=<the region from step 2>
   BACKUP_S3_BUCKET=metorite-backups
   BACKUP_S3_ACCESS_KEY_ID=<the key ID from step 2>
   BACKUP_S3_SECRET_ACCESS_KEY=<the secret from step 2>
   BACKUP_GPG_RECIPIENT=<the fingerprint that step 3 printed>
   BACKUP_GPG_PUBLIC_KEY_FILE=/opt/acb/backup-public-key.asc
   ```

   Then prove it at once. Do not wait for 02:30:

   ```bash
   sudo stat -c '%U:%G %a' /etc/acb/backup-offbox.env    # expect root:root 600
   sudo systemctl start acb-backup.service
   sudo journalctl -u acb-backup -n 80 --no-pager | grep -E 'off-box copy ok|ERROR'
   ```

   Expect `off-box copy ok (offbox:metorite-backups/nightly/<stamp>, N files, <size>)`.

#### The owner steps to restore

`scripts/restore_offbox.sh` lists the nights, and it downloads, decrypts and
checks one. It refuses a night that fails `SHA256SUMS` or `MANIFEST.txt`, a
file that `SHA256SUMS` does not list, and a disk with too little room. It
keeps the private key in `/dev/shm`, in memory only. It needs `rclone`, `gpg`,
`zstd` and `sha256sum`. The box has all four. On the box:

```bash
# 1. Bring the private key, and its passphrase, into memory only.
scp metorite-backups-PRIVATE.asc metorite:/dev/shm/k.asc
ssh metorite
printf '%s\n' '<the passphrase>' > /dev/shm/p.txt && chmod 600 /dev/shm/k.asc /dev/shm/p.txt

# 2. See the nights. A night with no SHA256SUMS is INCOMPLETE.
sudo bash /opt/acb/app/scripts/restore_offbox.sh --env-file /etc/acb/backup-offbox.env --list

# 3. Get one night, decrypt it and verify it. Use a stamp in place of latest.
sudo bash /opt/acb/app/scripts/restore_offbox.sh --env-file /etc/acb/backup-offbox.env \
  --night latest --key /dev/shm/k.asc --passphrase-file /dev/shm/p.txt \
  --out /opt/acb/restore-offbox

# 4. Remove the key and the passphrase at once.
shred -u /dev/shm/k.asc /dev/shm/p.txt

# 5. Restore into a SCRATCH database first, as §3 says.
sudo BACKUP_DIR=/opt/acb/restore-offbox bash /opt/acb/app/scripts/restore_db.sh --from <stamp>
# The file data: tar -C <scratch dir> -xf /opt/acb/restore-offbox/<stamp>/files.tar
```

If the VPS is gone, do the same steps on any Linux machine. Install `rclone`,
`gnupg` and `zstd`, and clone the repository. Put the `BACKUP_S3_*` values in
a file, and give that file to `--env-file`. If you do not have the S3 secret,
make a new key on the S3 Connection page.

On a machine with no `/dev/shm`, name a RAM disk with `--keyring-parent`. The
plain files hold customer data. Delete them when the restore is done.

### 4.3 PITR — deliberately NOT attempted

Point-in-time recovery needs `archive_mode = on`, an `archive_command`, a WAL
destination with real capacity, and **a Postgres restart**. That is a
production database restart plus a storage commitment, and it is a poor trade
before §4.1 and §4.2 exist. Nightly verified dumps take the RPO from ~7 days to
≤24 hours; PITR would take it to ~minutes, and is the right *next* step, not
the first one.

## 5. Unit files under `deploy/` — CREATED 2026-08-07

Both now exist at `deploy/hostinger/acb-backup.service` and
`deploy/hostinger/acb-backup.timer`, verbatim as below. `deploy.sh` installs
every `.service`/`.timer` in that directory into `/etc/systemd/system` on each
deploy, reloads systemd when a file actually changed, and `enable --now`s the
timers — so a unit added to the repo reaches the box with the code rather than
waiting for someone to remember. (`acb-health-watchdog.timer` was in the same
position and is picked up by the same loop.)

Kept below as the reference copy.

**`deploy/hostinger/acb-backup.service`**

```ini
[Unit]
# BO-23 — nightly application-level Postgres backup.
# See scripts/backup_db.sh for why the Hostinger VM image is not sufficient.
Description=Metorite database backup (pg_dump + integrity check)
# The dump talks to the acb-postgres container, so Docker must be up first.
# Without this the unit races the Docker daemon on boot and exits 1 on
# "container is not running" — a failure that looks like a broken backup
# rather than a mis-ordered unit.
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
# Root: needs `docker exec` and write access to /opt/acb/backups.
User=root
# Invoked through bash on purpose — a checkout that loses the executable bit
# (Windows contributors, archive exports) would otherwise fail with a bare
# "Permission denied" and backups would silently never run. Same reasoning as
# acb-health-watchdog.service.
ExecStart=/bin/bash /opt/acb/app/scripts/backup_db.sh --verify-restore
TimeoutStartSec=1800
```

**`deploy/hostinger/acb-backup.timer`**

```ini
[Unit]
Description=Nightly Metorite database backup
Documentation=file:///opt/acb/app/project-docs/specs/backup_and_restore.md

[Timer]
# 02:30 UTC — clear of the 05:00 UTC Monday codebase-health job, so a long
# verify restore cannot contend with it.
OnCalendar=*-*-* 02:30:00
# The box does get rebooted. Persistent=true runs a missed backup on the next
# boot instead of silently skipping a day — the exact gap you would only
# discover while trying to restore it.
Persistent=true
RandomizedDelaySec=5min
AccuracySec=1min
Unit=acb-backup.service

[Install]
WantedBy=timers.target
```

## 6. Verification owed

Once §4.1 is done, the ticket is closed by evidence, not by the files existing:

```bash
# 1. a backup exists and self-verified
sudo systemctl start acb-backup.service && journalctl -u acb-backup -n 40 --no-pager
#    expect: "restore verified" and "public tables: live=N restored=N"

# 2. a restore actually produces the data
scripts/restore_db.sh
docker exec acb-postgres psql -U acb -d acb_restored_<ts> \
  -tAc "select count(*) from app_user"
#    expect: matches MANIFEST.txt's app_user count

# 3. the timer is armed
systemctl list-timers acb-backup.timer
```

### 6.1 What is already verified, and what is not — 2026-08-07

**The tooling is tested.** `scripts/rehearse_restore.sh` does the full round
trip against a real Postgres — seed known rows, `backup_db.sh --verify-restore`
(the same command the systemd unit runs), `DROP` the table, `restore_db.sh`,
then compare an **md5 of the restored rows against the originals**. That last
comparison is the one that matters: every preceding step also passes against an
empty database, which is the failure you must never accept from a restore.

It additionally asserts the live database is untouched by a default restore,
and that a *truncated* dump is rejected — a verifier that passes on garbage is
worse than no verifier, because it converts an unnoticed problem into a false
assurance.

It runs in CI on every PR (`pr-check.yml` → "Backup/restore rehearsal"), and it
was checked in **both directions**: with `restore_db.sh` deliberately sabotaged
to create the scratch database without restoring into it, the rehearsal exits 1
with `restored 0 rows, backed up 250`.

This was only possible after both scripts stopped reaching Postgres exclusively
through `docker exec acb-postgres` — the coupling that meant they could not be
run anywhere but the VPS, which is exactly why they never had been. They now go
through a `pg`/`pgi` seam with `PG_MODE=local`; the VPS path is unchanged.

**What remains owed, and what no test can supply.** Steps 1–3 above, once, on
the box. The rehearsal proves the scripts are correct; it cannot prove that
*this* deployment's dump contains what you believe it does, that
`/opt/acb/backups` has room for fourteen of them, or that `BACKUP_REMOTE`
points anywhere at all. Those are properties of the machine.

Until step 2 has been run once against a **production** dump, this system has a
tested restore *path* but an unverified restore of *your data* — a materially
better position than BO-23 was filed against, and still not the finished one.

### 6.2 The off-box round trip — 2026-10-08

`scripts/rehearse_offbox.sh` runs the real tools against a real S3 server. On
2026-10-08 it ran in an `ubuntu:24.04` container, with the apt versions that
the box gets: rclone 1.60.1, GnuPG 2.4.4 and zstd 1.5.5. The S3 server was
MinIO (`pgsty/minio`, because MinIO no longer publishes its own image). The
database was `postgres:16`. The run was repeated after fix round 1. It proved five things:

1. `backup_db.sh` sent one night, and each of the 5 objects is an OpenPGP
   message to the test public key. No object is plaintext.
2. `restore_offbox.sh` listed the night, then downloaded, decrypted and
   verified it against `SHA256SUMS` and `MANIFEST.txt`.
3. The restored dump gave back the same 250 rows (the same md5), and
   `files.tar` gave back the same attachment.
4. With `BACKUP_S3_KEEP=2`, three complete nights became two. An old
   incomplete night went, and the newest incomplete night stayed. A folder
   that is not a night, and a night under another prefix, stayed.
5. A run without `--offbox` uploaded nothing. A key file in mode 0644 was
   refused. With no key, the run failed. No night reached the bucket in
   these three runs.

**What it does not prove.** The run used no meeting-bot volume, and it used
MinIO, not Supabase. The first real night on the box is the evidence for
both: the journal line `off-box copy ok`.
