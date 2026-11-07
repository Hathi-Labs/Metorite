# Box timers

User units for the home box (user `dev`). Install one with:

    mkdir -p ~/.config/systemd/user
    cp scripts/home_server/systemd/metorite-reaper.* ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now metorite-reaper.timer

Run `python3 scripts/reaper.py --dry-run` first. It lists what the reaper would
stop or remove. Read the log with `journalctl --user -u metorite-reaper`.

## Left loops and test runs (CIV-16)

The reaper also finds the processes that an agent left behind. A shell wait
loop (`until` or `while`, with `sleep`) or a `pytest` run is a candidate. It
must belong to the user of the reaper, and it must be older than its limit.

| Process | Limit |
|---|---|
| Wait loop whose parent is init | 2 h |
| Other wait loop | 6 h |
| `pytest` run | 3 h |

It stops a candidate only when its session ended or is idle:

- **Ended.** No `claude` process is above it, and each parent up to init is a
  shell or a wrapper (`bash`, `timeout`, `uv`, `nohup`).
- **Idle.** Each `claude` session above it has no process younger than
  10 minutes, and its status is `idle`. Its last transcript write is older than
  the limit, and older than 30 minutes. The reaper reads
  `~/.claude/sessions/<pid>.json` and the transcript that it names.

When the reaper cannot tell, it keeps the process. A parent that is not a
shell (tmux, sshd, systemd, a node host) is such a case. An empty transcript
(a remote session) is one too. It never touches the reaper or its ancestors.

The timer only LISTS these processes. To stop them, add `--apply` to
`ExecStart`. A stop sends SIGTERM to that one pid and waits 10 seconds. Then
the reaper sends SIGKILL, but only to the same process (same pid, same start
time). Each stop goes to the journal as `reaper: stop pid ...`.

    python3 scripts/reaper.py --count     # bash, sleep, node, claude, pytest of this user

**The PC.** Windows has no `/proc`, and Git Bash `ps` has no `etimes`. There
the reaper prints `no /proc on this host: nothing to do` and exits 0. Under WSL
it works as on the box.
