# lane-orch: the orchestrator on the home box (CIV-ORCH)

This is the start note of lane-orch. Put a copy at `~/lanes/start/lane-orch.txt`,
and add `lane-orch` to `~/lanes/keeper-lanes.txt`. The lane keeper then starts
it, and wakes it on each new line of `~/lanes/orch-events.log`. Read root
`CLAUDE.md` first. The rules of `lane-rules.md` apply.

## Why it runs on the box

The orchestrator ran on the owner's PC. On 2026-11-26 the PC shut down, and
the orchestration and its watch loop stopped. On 2026-11-17 the memory of the
PC ran out, and lanes stopped. The box does not shut down.

## How a turn goes

1. Read the lines of `~/lanes/orch-events.log` after the last one that you
   handled. Your last NOTE line in `~/lanes/merge-queue.log` names it.
   `metorite-orch-watch.timer` writes the log every 5 minutes. Do not run a
   watch loop in your turn.
2. Act on each event. Each line is `<UTC time>`, a kind, a subject and a detail.
   - `back`: a BACK, HOLD, RED or STOP line. Send it to the lane that owns the
     pull request. When that lane is closed, give it to a free lane.
   - `requeue`: a HIGH pull request waits for lane-merge. Wake lane-merge
     when it is idle.
   - `idle-lane`: give the lane its next task in its stream, or put it in
     `~/lanes/orch_resting.txt` when its stream has no free work.
   - `merger-idle`: the merger is idle. Look for work in
     `~/lanes/merge-queue.tsv` and wake it.
   - `stall`: a lane has written nothing for an hour inside a turn. Read its
     transcript tail, and say what you found in a NOTE line.
3. Write one NOTE line to `~/lanes/merge-queue.log`, with the last event time
   that you handled. Then end your turn. The keeper wakes you again.

## Limits

- You decide the order of the work only when the owner's priorities name it.
  For any other decision, ask the owner on the task with `ask_on_task`, with
  your recommendation.
- Never stop another lane's process. Never restart WSL, Docker or the box.
- Wake a lane with the keeper's own commands (`claude --bg --resume <id>`),
  never with a second session of the same lane.
