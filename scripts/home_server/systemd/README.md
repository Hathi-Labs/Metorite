# Box timers

User units for the home box (user `dev`). Install one with:

    mkdir -p ~/.config/systemd/user
    cp scripts/home_server/systemd/metorite-reaper.* ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now metorite-reaper.timer

Run `python3 scripts/reaper.py --dry-run` first. It lists what the reaper would
stop or remove. Read the log with `journalctl --user -u metorite-reaper`.
