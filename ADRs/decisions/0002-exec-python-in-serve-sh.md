# 0002: `exec` the Python Server in `serve.sh`

## Status
Accepted

## Date
2026-06-21

## Context

`serve.sh` ends with `python3 "${SCRIPT_DIR}/server.py"` (no `exec`). Bash forks Python as a child and blocks. Two PIDs exist for the lifetime of the server: the wrapper shell and the Python process.

Observed friction:
- Process listings show two Scribe-related entries; the wrapper is noise.
- `pkill -f "python3.*server.py"` kills the child but leaves the bash parent waiting on a stale handle until it's separately reaped or the terminal exits.
- Stopping the server with Ctrl-C in a foreground run works either way, but signal forwarding through bash is one extra hop that occasionally swallows or reorders signals when scripts grow.

## Decision Drivers

- **Should:** One PID per logical server. The wrapper has no remaining responsibility after it has launched Python.
- **Should not:** Change the wrapper's user-facing contract (`bash serve.sh` still starts the server and opens the browser).
- **Should not:** Break existing tooling that targets `python3.*server.py` by name; the Python process is unaffected.

## Decision

Replace the final line of `serve.sh` from `python3 "${SCRIPT_DIR}/server.py"` to `exec python3 "${SCRIPT_DIR}/server.py"`. Bash replaces itself with the Python process, leaving exactly one PID.

## Alternatives Considered

### A. (Chosen) `exec` the Python call
The wrapper has no work to do after launching Python. `exec` is the canonical Unix way to hand off a shell to its final long-running command.

### B. Keep two processes
Rejected. No code runs after the `python3` line, so the bash parent contributes nothing while the server runs. The double-PID surface is pure noise.

### C. Rewrite `serve.sh` in Python
Rejected. The wrapper's job (regenerate graph data, then start the server, then open a browser) is naturally a shell script. Migrating to Python for one line is over-engineering.

## Consequences

**Positive:** One PID. `pkill` and `ps` listings stop showing the wrapper. Signals reach Python directly.

**Negative:** Any code added after the `python3` line in the future will silently be unreachable — `exec` ends the script. A code comment above the line warns about this.

**Risks:** None observed. Confirmed locally that the browser-open command runs before the `exec` (it has to — `exec` never returns).

## Implementation Plan

- [x] Edit `serve.sh`: prefix the final `python3 …` invocation with `exec`.
- [x] Add a one-line comment above it noting that nothing after this line runs.
- [x] Verify: `bash serve.sh` boots Python; `ps -ax | grep server.py` shows exactly one PID; the browser still opens.

## Build Log

```
EVENT
problem: two PIDs for one logical server (bash wrapper + python child)
solution: exec python3 in serve.sh so bash replaces itself with python
tests: ps -ax shows one PID after launch; browser still opens
outcome: clean process tree; pkill no longer leaves an orphan wrapper
```
