#!/usr/bin/env python3
"""FlightLens autonomous orchestration lane.

Each invocation = one run:
  1. Read AUTONOMOUS_RUN_STATE.md (source of truth for progress)
  2. Pick the next unchecked packet
  3. Dispatch it to `claude --print` (Claude Code headless) if authed
  4. Run verification gates (pytest in repo venv)
  5. On pass: commit, write evidence, append run record to state file
  6. Log everything to logs/ so Barish can supervise via logs.

Designed to be driven by hermes cron every 45 min. Idempotent: if a run is
in-flight (lock file), exits quietly. If no packet remains, does repo hygiene.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/root/projects/flightlens")
STATE = REPO / "AUTONOMOUS_RUN_STATE.md"
EVIDENCE = REPO / "evidence"
LOGDIR = REPO / "logs"
LOCK = LOGDIR / "orchestrator.lock"
PACKETS = {
    "B": "Tasks 1+2 — synthetic fixture generator + loader",
    "C": "Tasks 3+4+5 — tracking, oscillation, vibration",
    "D": "Tasks 6+7 — verdict engine + CLI/report",
    "E": "Task 8+9 — real-log integration test + README/CI push",
}
CLAUDE_PING = ["timeout", "90", "claude", "-p", "Reply with exactly: OK",
               "--model", "claude-haiku-4-5"]


def log(msg: str) -> None:
    LOGDIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOGDIR / "orchestrator.log", "a") as f:
        f.write(line + "\n")


def run(cmd: list[str], cwd: Path = REPO, env_extra: dict | None = None,
        tee: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                       timeout=timeout)
    if tee:
        for stream, name in ((p.stdout, "stdout"), (p.stderr, "stderr")):
            if stream.strip():
                log(f"$ {' '.join(cmd)}\n{name}:\n{stream[:4000]}")
    return p


def claude_authed() -> bool:
    p = subprocess.run(CLAUDE_PING, capture_output=True, text=True)
    ok = p.returncode == 0 and "OK" in p.stdout
    log(f"claude auth ping: rc={p.returncode} ok={ok} out={p.stdout.strip()[:100]} {p.stderr.strip()[:200]}")
    return ok


def next_packet() -> tuple[str, str] | None:
    state = STATE.read_text()
    for key, desc in PACKETS.items():
        if f"- [ ] Packet {key}" in state:
            return key, desc
    return None


def build_prompt(packet: str, desc: str) -> str:
    plan = (REPO / "docs/plans/2026-10-08-flightlens-v0.1.md").read_text()
    # Extract only the relevant tasks to keep context lean (learned from
    # 2026-10-09 zero-output budget burn: don't make the agent read everything).
    task_lines = {
        "B": ("### Task 1", "### Task 3"),
        "C": ("### Task 3", "### Task 6"),
        "D": ("### Task 6", "### Task 8"),
        "E": ("### Task 8", "## Execution"),
    }
    start, end = task_lines[packet]
    plan_excerpt = plan[plan.index(start):plan.index(end)]

    return f"""You are implementing one packet of the FlightLens v0.1 plan in {REPO}.

Work autonomously. TDD mandatory. Commit when gates pass.

Setup:
- Venv at {REPO}/.venv (Python 3.11). Install deps first: .venv/bin/pip install -e . pymavlink pandas scipy typer rich pytest
- Scaffold pyproject.toml / src/flightlens package layout if missing (see plan Task 0 in full plan: docs/plans/2026-10-08-flightlens-v0.1.md). Prioritize conventional commits.

Packet {packet}: {desc}

{plan_excerpt}

Rules:
- Write code under src/flightlens/, tests under tests/. Fixture generator too big for git? fixtures/ gitignored, generator in tests/fixtures/ or conftest.
- Gate: .venv/bin/pytest -x -q must pass before commit.
- Never touch docs/plans/2026-10-08-flightlens-v0.1.md; do not edit AUTONOMOUS_RUN_STATE.md (the orchestrator owns it).
- Commit message: feat/fix style per plan.
- If blocked: write BLOCKED.md in repo root explaining exactly why, exit.
- At end print one line: RESULT: COMMIT_OK <sha>  OR  RESULT: BLOCKED <reason>
"""


def run_packet(packet: str, desc: str) -> bool:
    prompt = build_prompt(packet, desc)
    log(f"dispatching packet {packet}: {desc}")
    prompt_file = LOGDIR / f"packet-{packet}-prompt.md"
    prompt_file.write_text(prompt)
    out_file = LOGDIR / f"packet-{packet}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log"

    if not claude_authed():
        log("claude NOT authed — writing STALLED notice and exiting")
        return False

    with open(out_file, "w") as out:
        env = os.environ.copy()
        env["HOME"] = "/home/flightlens"  # worker user home (CC credentials live there)
        env["CLAUDECODE"] = "1"  # marker; also disable interactive bits
        # Run as the dedicated non-root user: bypassPermissions is refused for
        # root, and a worker sandbox is cleaner anyway.
        p = subprocess.run(
            ["sudo", "-u", "flightlens", "env", "HOME=/home/flightlens",
             "/home/flightlens/.local/bin/claude", "-p", prompt,
             "--permission-mode", "bypassPermissions"],
            cwd=REPO, stdout=out, stderr=subprocess.STDOUT, text=True,
            timeout=5400, env=env,
        )
    log(f"packet {packet} worker exited rc={p.returncode}, transcript: {out_file}")
    content = out_file.read_text(errors="replace")
    result = [l for l in content.splitlines() if l.startswith("RESULT:")]
    log(f"RESULT lines: {result[-3:] if result else 'none'}")
    return any("COMMIT_OK" in l for l in result)


def gates() -> bool:
    venv = REPO / ".venv"
    if not venv.exists():
        return False
    p = run([str(venv / "bin/pytest"), "-x", "-q"], timeout=1200)
    return p.returncode == 0


def record(packet: str, desc: str, status: str, extra: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git_sha = run(["git", "log", "-1", "--format=%h %s"], tee=False).stdout.strip()
    git_files = run(["git", "show", "--name-only", "--format=", "HEAD"], tee=False).stdout.strip()
    body = f"""
- UTC timestamp: {ts}
- packet: Packet {packet} — {desc}
- commit: {git_sha}
- changed files: {git_files}
- gates: see above
- status: {status}
{extra}
"""
    _ = run(["git", "add", "-A", "AUTONOMOUS_RUN_STATE.md", "evidence", "orchestrator", "logs"], tee=False)
    run(["git", "commit", "-m", f"docs: record FlightLens packet {packet} {status.lower()}",
         "--", "AUTONOMOUS_RUN_STATE.md", "evidence/", "orchestrator/", "logs/"], tee=False)
    # rewrite state with the checkbox flipped if success
    if status == "COMMIT_OK":
        state = STATE.read_text()
        state += body
        state = state.replace(f"- [ ] Packet {packet}", f"- [x] Packet {packet} (done {ts})")
        STATE.write_text(state)
        run(["git", "add", "AUTONOMOUS_RUN_STATE.md"], tee=False)
        run(["git", "commit", "--amend", "--no-edit", "--", "AUTONOMOUS_RUN_STATE.md"], tee=False)
    else:
        state = STATE.read_text()
        state += body
        STATE.write_text(state)


def main() -> int:
    LOGDIR.mkdir(exist_ok=True)
    EVIDENCE.mkdir(exist_ok=True)
    with open(LOCK, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another orchestrator run in flight; exiting")
            return 0
        log("=== orchestrator run start ===")
        pkt = next_packet()
        if pkt is None:
            log("all packets done — hygiene mode")
            run(["git", "status", "-s"])
            return 0
        ok = run_packet(*pkt)
        if not ok:
            record(pkt[0], pkt[1], "BLOCKED", extra="- exact next action: ensure claude auth is live, or re-dispatch\n")
            return 2
        if gates():
            record(pkt[0], pkt[1], "COMMIT_OK", extra="- exact next action: dispatch next packet\n")
            return 0
        record(pkt[0], pkt[1], "GATES_FAILED", extra="- exact next action: fix failing tests manually, then re-run\n")
        return 3


if __name__ == "__main__":
    sys.exit(main())
