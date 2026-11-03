#!/usr/bin/env bash
# Make this computer the Metorite home server: the dev, CI and staging box
# that the owner's PC drives over SSH (task 1534). It replaces the dev VPS
# srv1747539.
#
# Run it as the user you made in the Ubuntu installer. Do not pipe it into
# bash. The easy way is from the PC, after the box
# joined Tailscale, because there you can click the links and paste the codes:
#
#   ssh -t acb@metorite-home "curl -fsSLo setup.sh https://raw.githubusercontent.com/Hathi-Labs/Metorite/mt-38b0acb6-home-server/scripts/home_server/setup.sh && bash setup.sh"
#
# It also runs at the box's own keyboard. It stops for three sign-ins:
# Tailscale (skipped when the box already joined), GitHub and Claude. Safe to
# run again: each step checks first, and a sign-in that is done is skipped.
#
# What it sets up:
#   - users acb (admin) and dev (agents), each with passwordless sudo, docker,
#     and the SSH keys that github.com/vjvarada.keys publishes
#   - SSH with keys only, Tailscale, ufw, security updates, no sleep
#   - Docker, git, gh, Node 20, uv with Python 3.12, Claude Code
#   - the repo at ~dev/Metorite
#   - the CI runner: user ci (no sudo) with its own clone, the target of
#     scripts/ci_local.sh
# The PC session does the rest over SSH: staging and the always-on Claude
# sessions.
#
# Ubuntu 24.04 LTS, the release production runs.
#
# Inside WSL on a Windows box, Windows runs Tailscale, the firewall and the
# power plan (remote_access.ps1), so those steps are skipped. Windows' own
# sshd holds port 22, so Linux sshd takes port 2222. The distro needs
# systemd. SETUP_NO_SIGNIN=1 skips the GitHub and Claude sign-ins, so the
# script runs with no terminal, as root, from `wsl.exe -u root`.
set -Eeuo pipefail

BOX_NAME="${BOX_NAME:-metorite-home}"
GH_KEYS_USER="${GH_KEYS_USER:-vjvarada}"
REPO_URL="https://github.com/Hathi-Labs/Metorite.git"
ADMIN_USER=acb
AGENT_USER=dev
CI_USER=ci  # runs local CI (scripts/ci_local.sh); no sudo, as on the PC
GIT_NAME="${GIT_NAME:-Vijay Raghav Varada}"
GIT_EMAIL="${GIT_EMAIL:-vjvarada@gmail.com}"
NODE_MAJOR=20        # pr-check pins node-version "20"
PYTHON_VERSION=3.12  # pyproject.toml: >=3.12,<3.14
LOG=/var/log/metorite-home-setup.log
NO_SIGNIN="${SETUP_NO_SIGNIN:-0}"

on_wsl() { grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; }
if on_wsl; then SSH_PORT=2222; else SSH_PORT=22; fi

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die()  { printf '\n\033[31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
trap 'die "line $LINENO failed. The full log is $LOG"' ERR

# A quiet step: its output goes to the log, and the end of it shows on a failure.
run() {
  if ! "$@" >>"$LOG" 2>&1; then
    tail -n 25 "$LOG" >&2
    die "this command failed: $*  (the full log is $LOG)"
  fi
}
apt_install() {
  run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}
home_of() { getent passwd "$1" | cut -d: -f6; }
# A login shell, so ~/.local/bin is on PATH.
as_user() { sudo -u "$1" -H bash -lc "$2"; }
claude_signed_in() { as_user "$AGENT_USER" 'claude auth status 2>/dev/null' | grep -q '"loggedIn": true'; }

preflight() {
  if [ "$NO_SIGNIN" != 1 ]; then
    [ -t 0 ] || die "run it as: bash setup.sh. Piping it into bash breaks the sign-in prompts."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = ubuntu ] || die "this script is for Ubuntu 24.04 LTS. This computer runs ${PRETTY_NAME:-an unknown system}."
  if [ "${VERSION_ID:-}" != "24.04" ]; then
    [ "$NO_SIGNIN" != 1 ] || die "this is Ubuntu ${VERSION_ID:-?}. Production runs 24.04."
    read -r -p "   This is Ubuntu ${VERSION_ID:-?}. Production runs 24.04. Continue? [y/N] " a
    case "$a" in y|Y) ;; *) exit 1 ;; esac
  fi
  if on_wsl && [ ! -d /run/systemd/system ]; then
    die "WSL runs this distro without systemd. Put [boot] systemd=true in /etc/wsl.conf, run 'wsl --terminate <distro>' in Windows, and run this again."
  fi
  if [ "$(id -u)" -ne 0 ]; then
    note "sudo asks for your password once."
    sudo -v
    ( while kill -0 "$$" 2>/dev/null; do sudo -n true; sleep 50; done ) 2>/dev/null &
  fi
  sudo touch "$LOG"
  sudo chown "$(id -un)" "$LOG"
  echo "---- $(date -u +%FT%TZ) setup.sh start" >>"$LOG"
  # Each `sudo -u dev` command starts here. dev cannot read your home folder.
  cd /
}

set_hostname() {
  say "Name this computer $BOX_NAME"
  [ "$(hostname)" = "$BOX_NAME" ] || run sudo hostnamectl set-hostname "$BOX_NAME"
  if grep -q '^127\.0\.1\.1' /etc/hosts; then
    run sudo sed -i "s/^127\.0\.1\.1.*/127.0.1.1 $BOX_NAME/" /etc/hosts
  else
    echo "127.0.1.1 $BOX_NAME" | run sudo tee -a /etc/hosts
  fi
}

base_packages() {
  say "Update the system and install the base tools (a few minutes)"
  run sudo apt-get update
  run sudo env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
  apt_install ca-certificates curl gnupg git git-lfs tmux jq unzip zip xz-utils \
    rsync build-essential pkg-config ripgrep htop ncdu lsof ethtool \
    postgresql-client openssh-server ssh-import-id ufw unattended-upgrades \
    python3 python3-venv
  run sudo dpkg-reconfigure -f noninteractive unattended-upgrades
}

power_and_limits() {
  say "Keep the computer awake, and raise the file-watch limits"
  run sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  # A closed laptop lid must not stop the box. This applies at the next boot,
  # because a restart of logind ends a desktop session.
  run sudo install -d /etc/systemd/logind.conf.d
  printf '%s\n' '[Login]' 'HandleLidSwitch=ignore' 'HandleLidSwitchExternalPower=ignore' \
    'HandleLidSwitchDocked=ignore' | run sudo tee /etc/systemd/logind.conf.d/10-metorite-home.conf
  # The Next.js dev server and vitest watch many files.
  printf '%s\n' 'fs.inotify.max_user_watches=524288' 'fs.inotify.max_user_instances=1024' \
    | run sudo tee /etc/sysctl.d/60-metorite-home.conf
  run sudo sysctl --system
}

make_users() {
  say "Make the users $ADMIN_USER (admin) and $AGENT_USER (agents)"
  local u tmp
  for u in "$ADMIN_USER" "$AGENT_USER"; do
    if ! id "$u" >/dev/null 2>&1; then
      run sudo useradd -m -s /bin/bash "$u"
      # No password, and not locked: the account signs in with a key only.
      run sudo usermod -p '*' "$u"
    fi
    run sudo usermod -aG sudo "$u"
    # The PC session and the agents run sudo with no terminal, so no password.
    # Check the file before it goes live: a bad sudoers file breaks sudo.
    tmp=$(mktemp)
    echo "$u ALL=(ALL) NOPASSWD:ALL" >"$tmp"
    run sudo visudo -cf "$tmp"
    run sudo install -m 0440 -o root -g root "$tmp" "/etc/sudoers.d/90-metorite-$u"
    rm -f "$tmp"
  done
}

import_keys() {
  say "Trust the SSH keys that github.com/$GH_KEYS_USER publishes"
  local u
  for u in "$ADMIN_USER" "$AGENT_USER"; do
    run as_user "$u" "ssh-import-id gh:$GH_KEYS_USER"
    sudo test -s "$(home_of "$u")/.ssh/authorized_keys" \
      || die "no key arrived for $u. Check that https://github.com/$GH_KEYS_USER.keys lists a key."
  done
  note "Keys now trusted:"
  sudo ssh-keygen -lf "$(home_of "$ADMIN_USER")/.ssh/authorized_keys" | sed 's/^/     /'
}

# On WSL this runs before openssh-server installs, so sshd never tries port 22.
sshd_conf() {
  run sudo install -d /etc/ssh/sshd_config.d
  # sshd reads the first value it finds, and 10- sorts before the installer's 50-.
  printf '%s\n' '# Written by scripts/home_server/setup.sh.' "Port $SSH_PORT" \
    'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' \
    | run sudo tee /etc/ssh/sshd_config.d/10-metorite-home.conf
}

harden_ssh() {
  say "SSH: keys only, no root login, port $SSH_PORT"
  sshd_conf
  run sudo install -d -m 0755 /run/sshd
  run sudo sshd -t
  if on_wsl; then
    # The socket unit can still ask for port 22. The plain service reads Port.
    # shellcheck disable=SC2024  # the log belongs to this user, so no sudo is needed to write it
    sudo systemctl disable --now ssh.socket >>"$LOG" 2>&1 || true
    run sudo systemctl enable ssh.service
  fi
  run sudo systemctl restart ssh
}

join_tailscale() {
  say "Join your Tailscale network (sign-in 1 of 3)"
  command -v tailscale >/dev/null || run bash -c "curl -fsSL https://tailscale.com/install.sh | sh"
  if tailscale status --json 2>/dev/null | jq -e '.BackendState == "Running"' >/dev/null; then
    note "Already joined."
    run sudo tailscale set --hostname="$BOX_NAME"
  else
    note "Scan the QR code with your phone, or open the link on any device."
    note "Sign in with the SAME Tailscale account as on your PC."
    # No --ssh. Tailscale SSH asks for a browser sign-in again every few hours,
    # and that stops the PC session's commands. Plain OpenSSH with keys runs
    # over the tailnet instead.
    sudo tailscale up --hostname="$BOX_NAME" --qr
  fi
  sudo tailscale set --auto-update >/dev/null 2>&1 || true
}

firewall() {
  say "Firewall: open to the tailnet, SSH only from the home network"
  run sudo ufw default deny incoming
  run sudo ufw default allow outgoing
  run sudo ufw allow in on tailscale0
  run sudo ufw allow 41641/udp  # Tailscale direct connections
  local net
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    run sudo ufw allow from "$net" to any port 22 proto tcp
  done
  run sudo ufw --force enable
  # Docker publishes a port past ufw. Bind staging to 127.0.0.1 or the
  # tailnet address, never to 0.0.0.0.
}

install_docker() {
  say "Install Docker"
  if ! command -v docker >/dev/null; then
    run sudo install -m 0755 -d /etc/apt/keyrings
    run sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    run sudo chmod a+r /etc/apt/keyrings/docker.asc
    # shellcheck disable=SC1091
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      | run sudo tee /etc/apt/sources.list.d/docker.list
    run sudo apt-get update
    apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  # Rotate container logs, so they cannot fill the disk.
  if [ ! -f /etc/docker/daemon.json ]; then
    echo '{"log-driver": "json-file", "log-opts": {"max-size": "50m", "max-file": "3"}}' \
      | run sudo tee /etc/docker/daemon.json
    run sudo systemctl restart docker
  fi
  run sudo usermod -aG docker "$ADMIN_USER"
  run sudo usermod -aG docker "$AGENT_USER"
}

install_gh() {
  say "Install the GitHub CLI"
  command -v gh >/dev/null && return 0
  run sudo install -m 0755 -d /etc/apt/keyrings
  run sudo curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
  run sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | run sudo tee /etc/apt/sources.list.d/github-cli.list
  run sudo apt-get update
  apt_install gh
}

install_node() {
  say "Install Node $NODE_MAJOR, the version pr-check pins"
  node --version 2>/dev/null | grep -q "^v$NODE_MAJOR\." && return 0
  local arch base file dir tmp
  case "$(dpkg --print-architecture)" in
    amd64) arch=x64 ;;
    arm64) arch=arm64 ;;
    *) die "nodejs.org has no Node build for $(dpkg --print-architecture)" ;;
  esac
  base="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"
  tmp=$(mktemp -d)
  run curl -fsSLo "$tmp/SHASUMS256.txt" "$base/SHASUMS256.txt"
  file=$(grep -oE "node-v${NODE_MAJOR}\.[0-9]+\.[0-9]+-linux-${arch}\.tar\.xz" "$tmp/SHASUMS256.txt" | head -n 1) || true
  [ -n "$file" ] || die "the Node $NODE_MAJOR download is not in $base/SHASUMS256.txt"
  run curl -fsSLo "$tmp/$file" "$base/$file"
  (cd "$tmp" && grep " $file\$" SHASUMS256.txt | sha256sum -c -) >>"$LOG" 2>&1 \
    || die "the Node download failed its checksum"
  # Leave the three top-level text files out of /usr/local. Exact names: a
  # wildcard would also drop each LICENSE inside npm.
  dir="${file%.tar.xz}"
  run sudo tar -xJf "$tmp/$file" -C /usr/local --strip-components=1 --no-same-owner \
    --exclude="$dir/CHANGELOG.md" --exclude="$dir/LICENSE" --exclude="$dir/README.md"
  rm -rf "$tmp"
}

# Ubuntu's .bashrc stops early in a shell with no prompt, so a plain
# `ssh dev@box cmd` never saw ~/.local/bin. Put the PATH line first.
path_first() {
  local rc
  rc="$(home_of "$1")/.bashrc"
  sudo grep -q 'metorite-home PATH' "$rc" 2>/dev/null && return 0
  # shellcheck disable=SC2016
  { echo 'export PATH="$HOME/.local/bin:$PATH"  # metorite-home PATH'; sudo cat "$rc" 2>/dev/null || true; } \
    | sudo -u "$1" tee "$rc.new" >/dev/null
  run sudo -u "$1" mv "$rc.new" "$rc"
}

agent_tools() {
  say "Install uv, Python $PYTHON_VERSION and Claude Code for $AGENT_USER"
  path_first "$ADMIN_USER"
  path_first "$AGENT_USER"
  run as_user "$AGENT_USER" 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
  run as_user "$AGENT_USER" "uv python install $PYTHON_VERSION"
  run as_user "$AGENT_USER" 'command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
  run sudo -u "$AGENT_USER" -H git config --global user.name "$GIT_NAME"
  run sudo -u "$AGENT_USER" -H git config --global user.email "$GIT_EMAIL"
  run sudo -u "$AGENT_USER" -H git config --global init.defaultBranch main
}

clone_repo() {
  local dir
  dir="$(home_of "$AGENT_USER")/Metorite"
  say "Clone the repo to $dir (a few minutes)"
  if sudo test -d "$dir/.git"; then
    note "Already there."
  else
    run sudo -u "$AGENT_USER" -H git clone "$REPO_URL" "$dir"
  fi
}

# The same CI runner as the PC's metorite-ci distro: a ci user with no sudo,
# its own clone at ~ci/metorite, and the job image. scripts/ci_local.sh on
# the PC reaches it as ci@metorite-home, port 2222.
ci_runner() {
  say "The CI runner: user $CI_USER, its clone and the job image (about 10 minutes)"
  if ! id "$CI_USER" >/dev/null 2>&1; then
    run sudo useradd -m -s /bin/bash "$CI_USER"
    run sudo usermod -p '*' "$CI_USER"
  fi
  run sudo usermod -aG docker "$CI_USER"
  run as_user "$CI_USER" "ssh-import-id gh:$GH_KEYS_USER"
  path_first "$CI_USER"
  run as_user "$CI_USER" 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
  # bootstrap.sh presets the PC's CI distro too. It clones to ~ci/metorite.
  sudo cat "$(home_of "$AGENT_USER")/Metorite/scripts/ci_pc/bootstrap.sh" | run sudo -u "$CI_USER" -H bash -s
}

github_login() {
  say "Sign in to GitHub as $AGENT_USER (sign-in 2 of 3)"
  if sudo -u "$AGENT_USER" -H gh auth status >/dev/null 2>&1; then
    note "Already signed in."
  else
    note "gh shows an 8-letter code and asks you to press Enter. Press Enter. No browser"
    note "opens here, so open https://github.com/login/device on your PC or phone, type"
    note "the code and approve. If gh asks about Git credentials, answer Y."
    sudo -u "$AGENT_USER" -H gh auth login --hostname github.com --git-protocol https --web --scopes workflow
  fi
  run sudo -u "$AGENT_USER" -H gh auth setup-git
}

claude_login() {
  say "Sign in to Claude as $AGENT_USER (sign-in 3 of 3)"
  if claude_signed_in; then
    note "Already signed in."
    return 0
  fi
  note "Claude shows a link. Open it, sign in, and paste the code it gives you here."
  note "At the box's own keyboard that is slow. You can skip it and run this from the PC:"
  note "    ssh -t $AGENT_USER@$BOX_NAME claude auth login"
  read -r -p "   Sign in to Claude now? [Y/n] " a
  case "$a" in n|N) return 0 ;; esac
  as_user "$AGENT_USER" 'claude auth login' || note "The Claude sign-in did not finish. Run the line above later."
}

summary() {
  local ts_name ts_ip
  ts_name=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//') || true
  ts_ip=$(tailscale ip -4 2>/dev/null | head -n 1) || true
  say "Finished"
  if on_wsl; then ts_name="through Windows, ssh port $SSH_PORT"; fi
  {
    echo "box:       $BOX_NAME"
    echo "tailnet:   ${ts_name:-NOT JOINED} ${ts_ip:-}"
    echo "users:     $ADMIN_USER (admin), $AGENT_USER (agents)"
    echo "docker:    $(docker --version 2>/dev/null || echo missing)"
    echo "node:      $(node --version 2>/dev/null || echo missing)"
    echo "gh:        $( (gh --version 2>/dev/null || echo missing) | head -n 1)"
    echo "uv:        $(as_user "$AGENT_USER" 'uv --version' 2>/dev/null || echo missing)"
    echo "claude:    $(as_user "$AGENT_USER" 'claude --version' 2>/dev/null || echo missing)"
    echo "github:    $(sudo -u "$AGENT_USER" -H gh auth status >/dev/null 2>&1 && echo signed in || echo NOT signed in)"
    echo "claude in: $(claude_signed_in && echo signed in || echo "NOT signed in: ssh -t $AGENT_USER@$BOX_NAME claude auth login")"
  } | tee -a "$LOG"
  echo
  note "Tell Claude on your PC: the home server is ready."
}

main() {
  preflight
  if on_wsl; then
    note "WSL: Windows runs Tailscale, the firewall and the power plan."
    sshd_conf
  else
    set_hostname
  fi
  base_packages
  make_users
  import_keys
  harden_ssh
  if ! on_wsl; then
    join_tailscale
    firewall
  fi
  power_and_limits
  install_docker
  install_gh
  install_node
  agent_tools
  clone_repo
  ci_runner
  if [ "$NO_SIGNIN" = 1 ]; then
    note "SETUP_NO_SIGNIN=1: sign in later with 'gh auth login' and 'claude auth login' as $AGENT_USER."
  else
    github_login
    claude_login
  fi
  summary
}

# Sourcing the file (the container test does) defines the steps and runs none.
# Piped into bash, BASH_SOURCE is empty: main runs, and preflight refuses it.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then main "$@"; fi
