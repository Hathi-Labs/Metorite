#!/usr/bin/env bash
# box: run the agent sessions and the Claude accounts on the home box from
# the owner's PC (task 1534). The box is metorite-home: WSL Ubuntu, user dev.
# The runbook is docs/home_box.md.
#
#   box sessions                 each session: state, task, Remote Control link
#   box tail <name|id> [n]       the last n messages of a session, as plain text
#   box start <name> <model> <prompt-file> [title]
#                                a background session with Remote Control, in ~/Metorite
#   box stop <name|id>           stop a background session (its transcript stays)
#   box respawn <name|id>        restart it in place, same id and options
#   box accounts                 the Hub's Claude accounts and their limits
#   box switch <email|auto>      make an account live. auto picks the most headroom,
#                                and switches only when the live one is near a limit.
#
# A switch moves every open session to the new account between steps (Claude Hub).
# accounts and switch need BOX_HUB (the Hub address on the tailnet). Set
# BOX_SWITCH_SKIP to the account that this PC's sessions use. This repo is
# public, so neither value is written here.
# The node -e programs below sit in single quotes on purpose: node expands them.
# shellcheck disable=SC2016
set -euo pipefail
HOST="${BOX_HOST:-metorite-home}"
HUB="${BOX_HUB:-}"
SECRET_FILE="${BOX_HUB_SECRET:-$HOME/.metorite/secrets/switcher-login.txt}"
JAR="${BOX_HUB_JAR:-$HOME/.metorite/secrets/box-hub-cookie.txt}"
HIGH="${BOX_SWITCH_AT:-90}"   # auto: switch when the live session or weekly use is at or above this
LOW="${BOX_SWITCH_TO:-75}"    # auto: only to an account below this on both
# auto never picks these (comma list): an account this PC's sessions use shares its limits.
SKIP="${BOX_SWITCH_SKIP:-}"

r() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$@"; }
die() { echo "box: $*" >&2; exit 1; }
need_hub() { [ -n "$HUB" ] || die "set BOX_HUB to the Hub address (https://<the box's tailnet name>)"; }

# The id of a session, from its name or a prefix of its id.
sid() {
  r python3 - "$1" <<'PY'
import json, subprocess, sys
want = sys.argv[1]
agents = json.loads(subprocess.run(["claude", "agents", "--json", "--all"], capture_output=True, text=True).stdout or "[]")
hits = [a for a in agents if a.get("name") == want or a.get("sessionId", "").startswith(want)]
if len(hits) != 1:
    sys.exit(f"no single session matches {want!r}: {[a.get('name') for a in hits] or 'none'}")
print(hits[0]["sessionId"][:8])
PY
}

sessions() {
  r python3 - <<'PY'
import glob, json, os, re, subprocess
agents = json.loads(subprocess.run(["claude", "agents", "--json", "--all"], capture_output=True, text=True).stdout or "[]")
home = os.path.expanduser("~/.claude/projects")
rc = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
task = re.compile(r"https://app\.metorite\.com/projects\?task=[0-9a-f-]{36}")
for a in agents:
    sid = a.get("sessionId", "")
    files = glob.glob(f"{home}/*/{sid}.jsonl")
    text = open(files[0], encoding="utf-8", errors="replace").read() if files else ""
    last = ""
    for line in reversed(text.splitlines()):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            parts = [c.get("text", "") for c in (m.get("message", {}).get("content") or []) if isinstance(c, dict) and c.get("type") == "text"]
            if any(p.strip() for p in parts):
                last = " ".join(" ".join(parts).split())[:160]
                break
    links = rc.findall(text)
    tasks = task.findall(text)
    print(f"{a.get('name') or '-':<18} {sid[:8]}  {a.get('kind', ''):<11} {a.get('status', ''):<8} {a.get('cwd', '')}")
    print(f"    app:  {links[-1] if links else '(no Remote Control link yet)'}")
    print(f"    task: {tasks[-1] if tasks else '(none yet)'}")
    if last:
        print(f"    last: {last}")
PY
}

tail_session() {
  local id; id="$(sid "$1")"
  r python3 - "$id" "${2:-6}" <<'PY'
import glob, json, os, sys
sid, n = sys.argv[1], int(sys.argv[2])
files = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}*.jsonl"))
if not files:
    sys.exit("no transcript yet")
out = []
for line in open(max(files, key=os.path.getmtime), encoding="utf-8", errors="replace"):
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("type") not in ("user", "assistant"):
        continue
    content = m.get("message", {}).get("content")
    if isinstance(content, str):
        texts = [content]
    else:
        texts = [c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text"]
    t = " ".join(" ".join(texts).split())
    if t:
        out.append(f"[{m['type']}] {t[:600]}")
print("\n".join(out[-n:]))
PY
}

start() {
  local name="$1" model="$2" file="$3" title="${4:-$1}"
  [ -f "$file" ] || die "no prompt file $file"
  tr -d '\r' < "$file" | r "mkdir -p ~/lanes && cat > ~/lanes/$name.txt"
  r "cd ~/Metorite && git fetch -q origin && claude --bg --remote-control $(printf %q "$title") -n $(printf %q "$name") --model $(printf %q "$model") --permission-mode auto \"\$(cat ~/lanes/$name.txt)\" 2>&1 | tail -4"
}

hub_login() {
  need_hub
  [ -s "$JAR" ] && curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$HUB/switcher/api/accounts" | grep -q 200 && return 0
  [ -s "$SECRET_FILE" ] || die "no Hub secret at $SECRET_FILE"
  mkdir -p "$(dirname "$JAR")"
  node -e "process.stdout.write(JSON.stringify({secret:require('fs').readFileSync(process.argv[1],'utf8').trim()}))" "$SECRET_FILE" |
    curl -s -o /dev/null -c "$JAR" -H 'x-switcher: 1' -H 'content-type: application/json' -H "origin: $HUB" --data-binary @- "$HUB/auth/secret"
  chmod 600 "$JAR" 2>/dev/null || true
}

accounts_json() { hub_login; curl -s -b "$JAR" "$HUB/switcher/api/accounts?refresh=${1:-0}"; }

accounts() {
  accounts_json 1 | node -e '
    let s=""; process.stdin.on("data",d=>s+=d).on("end",()=>{ const d=JSON.parse(s);
      for (const a of d.accounts) { const u=a.usage||{}; const p=w=>w?Math.round(w.pct)+"%":"-";
        console.log(`${a.live?"* ":"  "}${(a.email||"?").padEnd(34)} ${(a.plan||"").padEnd(8)} session ${p(u.session).padStart(4)}  weekly ${p(u.weekly).padStart(4)}  ${u.error||""}`); }
      if (d.mismatch) console.log("WARNING: the live token belongs to " + d.tokenOwner); });'
}

switch() {
  local target="$1" body
  if [ "$target" = auto ]; then
    target="$(accounts_json 1 | HIGH="$HIGH" LOW="$LOW" SKIP="$SKIP" node -e '
      let s=""; process.stdin.on("data",d=>s+=d).on("end",()=>{ const d=JSON.parse(s);
        const use=a=>Math.max(a.usage?.session?.pct ?? 100, a.usage?.weekly?.pct ?? 100);
        const live=d.accounts.find(a=>a.live);
        if (live && use(live) < +process.env.HIGH) { console.error(`live ${live.email} is at ${Math.round(use(live))}%, under ${process.env.HIGH}%: no switch`); return; }
        // Use it or lose it: among the accounts with headroom, the one whose weekly quota
        // resets soonest goes first. An account with no reset time goes last.
        const reset=a=>Date.parse(a.usage?.weekly?.resetsAt||"") || Infinity;
        const skip=new Set((process.env.SKIP||"").toLowerCase().split(",").filter(Boolean));
        const best=d.accounts.filter(a=>!a.live && a.switchable && !a.usage?.error && !skip.has((a.email||"").toLowerCase()) && use(a) < +process.env.LOW).sort((x,y)=>reset(x)-reset(y) || use(x)-use(y))[0];
        if (!best) { console.error("no saved account has headroom below " + process.env.LOW + "%"); return; }
        process.stdout.write(best.email); });')"
    [ -n "$target" ] || return 0
  fi
  body="$(accounts_json | TARGET="$target" node -e '
    let s=""; process.stdin.on("data",d=>s+=d).on("end",()=>{ const d=JSON.parse(s);
      const a=d.accounts.find(x=>(x.email||"").toLowerCase()===process.env.TARGET.toLowerCase());
      if (!a) { console.error("no saved account " + process.env.TARGET); process.exit(1); }
      process.stdout.write(JSON.stringify({dir:a.dir})); });')"
  curl -s -b "$JAR" -H 'x-switcher: 1' -H 'content-type: application/json' -H "origin: $HUB" --data-binary "$body" "$HUB/switcher/api/switch"
  echo
}

cmd="${1:-sessions}"; shift || true
case "$cmd" in
  sessions) sessions ;;
  tail) [ $# -ge 1 ] || die "usage: box tail <name|id> [n]"; tail_session "$@" ;;
  start) [ $# -ge 3 ] || die "usage: box start <name> <model> <prompt-file> [title]"; start "$@" ;;
  stop) [ $# -eq 1 ] || die "usage: box stop <name|id>"; r "claude stop $(sid "$1")" ;;
  respawn) [ $# -eq 1 ] || die "usage: box respawn <name|id>"; r "claude respawn $(sid "$1")" ;;
  accounts) need_hub; accounts ;;
  switch) [ $# -eq 1 ] || die "usage: box switch <email|auto>"; need_hub; switch "$1" ;;
  *) sed -n '2,21p' "$0"; exit 1 ;;
esac
