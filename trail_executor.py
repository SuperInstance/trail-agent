"""
Trail Execution Engine — replays compiled trail bytecode as real operations.

Standalone module extracted from holodeck-studio trail_executor.py.
Zero external dependencies — uses only Python stdlib.

Design:
    - TrailExecutor takes compiled bytecode + WorldInterface
    - WorldInterface provides the actual implementations (git, file, test, etc.)
    - Execution is sandboxed: every operation goes through the WorldInterface
    - Execution is observable: every step emits a TrailEvent for auditing
    - Execution is resumable: can pause/resume at any step
    - Execution is verifiable: the executor logs what it did, producing a new trail

Execution Model:
    bytecode -> TrailDecoder -> TrailSteps -> step through each -> WorldInterface.*()
                                                                    |
                                                        execution_trail (new bytecode)
                                                        execution_fingerprint (SHA-256)

Error Handling:
    - Steps can fail (file not found, test failure, etc.)
    - On failure: record the error event, but CONTINUE to next step
    - Can configure fail-fast mode (stop on first error)
    - Can configure dry-run mode (log but don't execute)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from trail_codec import (
    TrailOpcodes,
    TrailStep,
    TrailProgram,
    TrailEncoder,
    TrailDecoder,
    str_to_hash,
    u16_pair_to_hex,
)
from trail_compiler import TrailCompiler


# ═══════════════════════════════════════════════════════════════════════════════
# Operand Type Signatures
# ═══════════════════════════════════════════════════════════════════════════════

OPCODE_ARG_TYPES: dict[TrailOpcodes, list[str]] = {
    TrailOpcodes.GIT_COMMIT:   ["n", "s"],
    TrailOpcodes.GIT_PUSH:     ["s"],
    TrailOpcodes.FILE_READ:    ["s"],
    TrailOpcodes.FILE_WRITE:   ["s", "s"],
    TrailOpcodes.FILE_EDIT:    ["s", "s", "s"],
    TrailOpcodes.TEST_RUN:     ["s", "n"],
    TrailOpcodes.SEARCH_CODE:  ["s"],
    TrailOpcodes.BOTTLE_DROP:  ["s", "s"],
    TrailOpcodes.BOTTLE_READ:  ["s"],
    TrailOpcodes.LEVEL_UP:     ["n"],
    TrailOpcodes.SPELL_CAST:   ["s"],
    TrailOpcodes.ROOM_ENTER:   ["s"],
    TrailOpcodes.TRUST_UPDATE: ["s", "n"],
    TrailOpcodes.CAP_ISSUE:    ["s", "s"],
    TrailOpcodes.BRANCH:       ["n"],
    TrailOpcodes.NOP:          [],
    TrailOpcodes.COMMENT:      ["s"],
    TrailOpcodes.LABEL:        ["s"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# TrailEvent
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrailEvent:
    """
    Emitted for each executed step during trail replay.
    Every operation produces a TrailEvent for audit logging.
    """
    step_index: int
    opcode: TrailOpcodes
    operands: dict[str, Any]
    result: str
    duration_ms: float
    timestamp: float
    proof: Optional[str] = None

    def __post_init__(self) -> None:
        """Compute proof hash if not provided."""
        if self.proof is None:
            self.proof = self._compute_proof()

    def _compute_proof(self) -> str:
        """SHA-256 proof of this event's result."""
        payload = f"{self.step_index}:{int(self.opcode)}:{self.result}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to a plain dict."""
        return {
            "step_index": self.step_index,
            "opcode": int(self.opcode),
            "opcode_name": self.opcode.name,
            "operands": self.operands,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "proof": self.proof,
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrailEvent:
        """Deserialize event from a dict."""
        return cls(
            step_index=data["step_index"],
            opcode=TrailOpcodes(data["opcode"]),
            operands=data["operands"],
            result=data["result"],
            duration_ms=data["duration_ms"],
            timestamp=data["timestamp"],
            proof=data.get("proof"),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TrailResult
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrailResult:
    """
    Complete result of a trail execution.
    Contains all events, statistics, and cryptographic proof.
    """
    success: bool
    total_steps: int
    completed_steps: int
    failed_steps: int
    events: list[TrailEvent]
    duration_ms: float
    execution_trail: bytes
    execution_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize result to a plain dict."""
        return {
            "success": self.success,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "duration_ms": self.duration_ms,
            "execution_fingerprint": self.execution_fingerprint,
            "execution_trail_size": len(self.execution_trail),
            "events": [e.to_dict() for e in self.events],
        }

    def to_json(self) -> str:
        """Serialize result to JSON."""
        return json.dumps(self.to_dict(), indent=2)

    def summary(self) -> str:
        """Produce a human-readable execution summary."""
        status = ("SUCCESS" if self.success
                  else "PARTIAL" if self.completed_steps > 0
                  else "FAILED")
        lines = [
            f"=== TRAIL EXECUTION {status} ===",
            f"  Total steps:    {self.total_steps}",
            f"  Completed:      {self.completed_steps}",
            f"  Failed:         {self.failed_steps}",
            f"  Duration:       {self.duration_ms:.1f} ms",
            f"  Fingerprint:    {self.execution_fingerprint[:32]}...",
            f"  Trail size:     {len(self.execution_trail)} bytes",
            "",
        ]
        for ev in self.events:
            icon = "+" if "error" not in ev.result.lower() else "x"
            lines.append(
                f"  [{icon}] #{ev.step_index:03d} "
                f"{ev.opcode.name:<14} {ev.duration_ms:.1f}ms  {ev.result[:60]}"
            )
        lines.append("")
        lines.append("=== END OF EXECUTION ===")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# WorldInterface Protocol
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class WorldInterface(Protocol):
    """
    The sandbox boundary for trail execution.
    Implementations: MockWorld (testing) or FileWorld (real filesystem).
    """
    def git_commit(self, repo: str, message: str) -> str: ...
    def git_push(self, repo: str) -> str: ...
    def file_read(self, path: str) -> str: ...
    def file_write(self, path: str, content: str) -> str: ...
    def file_edit(self, path: str, old: str, new: str) -> str: ...
    def test_run(self, test_path: str, expected: int) -> str: ...
    def search_code(self, pattern: str) -> str: ...
    def bottle_drop(self, target: str, content: str) -> str: ...
    def bottle_read(self, source: str) -> str: ...
    def level_up(self, agent: str, level: int) -> str: ...
    def spell_cast(self, spell: str) -> str: ...
    def room_enter(self, room: str) -> str: ...
    def trust_update(self, target: str, delta: float) -> str: ...
    def cap_issue(self, action: str, holder: str) -> str: ...


# ═══════════════════════════════════════════════════════════════════════════════
# MockWorld — Test Implementation
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MockWorld:
    """
    Test implementation of WorldInterface that records calls without side effects.
    Tracks all calls in order, can simulate failure, returns configurable results.
    """
    calls: list[dict[str, Any]] = field(default_factory=list)
    call_results: dict[str, str] = field(default_factory=dict)
    fail_on: Optional[set[str]] = None

    def _record(self, method: str, args: dict[str, Any]) -> str:
        """Record a call and return configured result or default."""
        self.calls.append({"method": method, "args": dict(args)})
        if self.fail_on and method in self.fail_on:
            raise RuntimeError(f"Simulated failure: {method}")
        return self.call_results.get(method, f"{method}: ok")

    def git_commit(self, repo: str, message: str) -> str:
        return self._record("git_commit", {"repo": repo, "message": message})

    def git_push(self, repo: str) -> str:
        return self._record("git_push", {"repo": repo})

    def file_read(self, path: str) -> str:
        return self._record("file_read", {"path": path})

    def file_write(self, path: str, content: str) -> str:
        return self._record("file_write", {"path": path, "content": content})

    def file_edit(self, path: str, old: str, new: str) -> str:
        return self._record("file_edit", {"path": path, "old": old, "new": new})

    def test_run(self, test_path: str, expected: int) -> str:
        return self._record("test_run", {"test_path": test_path, "expected": expected})

    def search_code(self, pattern: str) -> str:
        return self._record("search_code", {"pattern": pattern})

    def bottle_drop(self, target: str, content: str) -> str:
        return self._record("bottle_drop", {"target": target, "content": content})

    def bottle_read(self, source: str) -> str:
        return self._record("bottle_read", {"source": source})

    def level_up(self, agent: str, level: int) -> str:
        return self._record("level_up", {"agent": agent, "level": level})

    def spell_cast(self, spell: str) -> str:
        return self._record("spell_cast", {"spell": spell})

    def room_enter(self, room: str) -> str:
        return self._record("room_enter", {"room": room})

    def trust_update(self, target: str, delta: float) -> str:
        return self._record("trust_update", {"target": target, "delta": delta})

    def cap_issue(self, action: str, holder: str) -> str:
        return self._record("cap_issue", {"action": action, "holder": holder})

    def assert_call_count(self, method: str, expected: int) -> None:
        """Assert a method was called exactly `expected` times."""
        actual = sum(1 for c in self.calls if c["method"] == method)
        if actual != expected:
            raise AssertionError(
                f"{method} called {actual} times, expected {expected}"
            )

    def assert_call_order(self, methods: list[str]) -> None:
        """Assert methods were called in the exact order given."""
        called = [c["method"] for c in self.calls]
        if called != methods:
            raise AssertionError(
                f"Call order mismatch.\n  Expected: {methods}\n  Got: {called}"
            )

    def assert_called_with(self, method: str, args: dict[str, Any]) -> None:
        """Assert the most recent call to `method` used these args."""
        for call in reversed(self.calls):
            if call["method"] == method:
                if call["args"] != args:
                    raise AssertionError(
                        f"{method} args mismatch.\n"
                        f"  Expected: {args}\n  Got: {call['args']}"
                    )
                return
        raise AssertionError(f"{method} was never called")

    def reset(self) -> None:
        """Clear all recorded calls and results."""
        self.calls.clear()
        self.call_results.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# FileWorld — Real Filesystem Implementation
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileWorld:
    """
    Real implementation of WorldInterface for local filesystem operations.

    - file_read/write/edit: real file I/O with optional backup
    - git_commit/push: subprocess calls to git
    - test_run: subprocess call to pytest
    - search_code: ripgrep (rg) with grep fallback
    - Other operations: no-ops with logging
    """
    base_dir: str = "."
    backup_on_write: bool = True
    calls: list[dict[str, Any]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def _safe_path(self, path: str) -> str:
        """Resolve path relative to base_dir."""
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.base_dir, path))

    def _record(self, method: str, args: dict[str, Any]) -> None:
        """Record a call for auditing."""
        self.calls.append({"method": method, "args": dict(args)})

    def file_read(self, path: str) -> str:
        full = self._safe_path(path)
        self._record("file_read", {"path": full})
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            self.log.append(f"Read {path} ({len(content)} chars)")
            return f"file_read: {path} ({len(content)} chars)"
        except FileNotFoundError:
            msg = f"file_read: ERROR file not found: {path}"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"file_read: ERROR {e}"
            self.log.append(msg)
            return msg

    def file_write(self, path: str, content: str) -> str:
        full = self._safe_path(path)
        self._record("file_write", {"path": full, "content_len": len(content)})
        try:
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if self.backup_on_write and os.path.exists(full):
                shutil.copy2(full, full + ".bak")
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            msg = f"file_write: {path} ({len(content)} chars)"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"file_write: ERROR {e}"
            self.log.append(msg)
            return msg

    def file_edit(self, path: str, old: str, new: str) -> str:
        full = self._safe_path(path)
        self._record("file_edit", {"path": full, "old_len": len(old), "new_len": len(new)})
        try:
            if self.backup_on_write and os.path.exists(full):
                shutil.copy2(full, full + ".bak")
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            if old not in content:
                msg = f"file_edit: ERROR old text not found in {path}"
                self.log.append(msg)
                return msg
            new_content = content.replace(old, new, 1)
            with open(full, "w", encoding="utf-8") as f:
                f.write(new_content)
            msg = f"file_edit: {path} (1 replacement)"
            self.log.append(msg)
            return msg
        except FileNotFoundError:
            msg = f"file_edit: ERROR file not found: {path}"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"file_edit: ERROR {e}"
            self.log.append(msg)
            return msg

    def git_commit(self, repo: str, message: str) -> str:
        repo_path = self._safe_path(repo)
        self._record("git_commit", {"repo": repo_path, "message": message})
        try:
            subprocess.run(["git", "add", "-A"], cwd=repo_path,
                           capture_output=True, text=True, timeout=30)
            result = subprocess.run(["git", "commit", "-m", message],
                                    cwd=repo_path, capture_output=True,
                                    text=True, timeout=30)
            if result.returncode == 0:
                msg = f"git_commit: {message}"
                self.log.append(msg)
                return msg
            else:
                msg = f"git_commit: ERROR {result.stderr.strip()}"
                self.log.append(msg)
                return msg
        except FileNotFoundError:
            msg = "git_commit: ERROR git not found"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"git_commit: ERROR {e}"
            self.log.append(msg)
            return msg

    def git_push(self, repo: str) -> str:
        repo_path = self._safe_path(repo)
        self._record("git_push", {"repo": repo_path})
        try:
            result = subprocess.run(["git", "push"], cwd=repo_path,
                                    capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                msg = f"git_push: {repo}"
                self.log.append(msg)
                return msg
            else:
                msg = f"git_push: ERROR {result.stderr.strip()}"
                self.log.append(msg)
                return msg
        except FileNotFoundError:
            msg = "git_push: ERROR git not found"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"git_push: ERROR {e}"
            self.log.append(msg)
            return msg

    def test_run(self, test_path: str, expected: int) -> str:
        full = self._safe_path(test_path)
        self._record("test_run", {"test_path": full, "expected": expected})
        try:
            result = subprocess.run(["python", "-m", "pytest", full, "-q", "--tb=no"],
                                    capture_output=True, text=True, timeout=120)
            msg = f"test_run: {test_path} (exit={result.returncode})"
            self.log.append(msg)
            return msg
        except FileNotFoundError:
            msg = "test_run: ERROR pytest not found"
            self.log.append(msg)
            return msg
        except Exception as e:
            msg = f"test_run: ERROR {e}"
            self.log.append(msg)
            return msg

    def search_code(self, pattern: str) -> str:
        self._record("search_code", {"pattern": pattern})
        try:
            result = subprocess.run(["rg", "-l", pattern, self.base_dir],
                                    capture_output=True, text=True, timeout=30)
            matches = (result.stdout.strip().split("\n")
                       if result.stdout.strip() else [])
            msg = f"search_code: '{pattern}' -> {len(matches)} matches"
            self.log.append(msg)
            return msg
        except FileNotFoundError:
            try:
                result = subprocess.run(["grep", "-rl", pattern, self.base_dir],
                                        capture_output=True, text=True, timeout=30)
                matches = (result.stdout.strip().split("\n")
                           if result.stdout.strip() else [])
                msg = f"search_code: '{pattern}' -> {len(matches)} matches (grep fallback)"
                self.log.append(msg)
                return msg
            except Exception as e:
                msg = f"search_code: ERROR {e}"
                self.log.append(msg)
                return msg
        except Exception as e:
            msg = f"search_code: ERROR {e}"
            self.log.append(msg)
            return msg

    def bottle_drop(self, target: str, content: str) -> str:
        self._record("bottle_drop", {"target": target, "content": content})
        self.log.append(f"bottle_drop: ->{target}")
        return f"bottle_drop: ->{target} ({len(content)} chars)"

    def bottle_read(self, source: str) -> str:
        self._record("bottle_read", {"source": source})
        self.log.append(f"bottle_read: <-{source}")
        return f"bottle_read: <-{source}"

    def level_up(self, agent: str, level: int) -> str:
        self._record("level_up", {"agent": agent, "level": level})
        self.log.append(f"level_up: {agent} -> level {level}")
        return f"level_up: {agent} -> level {level}"

    def spell_cast(self, spell: str) -> str:
        self._record("spell_cast", {"spell": spell})
        self.log.append(f"spell_cast: {spell}")
        return f"spell_cast: {spell}"

    def room_enter(self, room: str) -> str:
        self._record("room_enter", {"room": room})
        self.log.append(f"room_enter: {room}")
        return f"room_enter: {room}"

    def trust_update(self, target: str, delta: float) -> str:
        self._record("trust_update", {"target": target, "delta": delta})
        self.log.append(f"trust_update: {target} {delta:+.1f}")
        return f"trust_update: {target} {delta:+.1f}"

    def cap_issue(self, action: str, holder: str) -> str:
        self._record("cap_issue", {"action": action, "holder": holder})
        self.log.append(f"cap_issue: {action} -> {holder}")
        return f"cap_issue: {action} ->{holder}"


# ═══════════════════════════════════════════════════════════════════════════════
# Operand Resolution
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_operands(
    opcode: TrailOpcodes,
    operands: list[int],
    string_table: dict[str, str],
) -> list[Any]:
    """
    Resolve raw u16 operands to their Python types using the string table.
    String args ('s') are u16 pairs; numeric args ('n') are single u16 values.
    """
    arg_types = OPCODE_ARG_TYPES.get(opcode, [])
    resolved: list[Any] = []
    pos = 0

    for arg_type in arg_types:
        if arg_type == "s":
            if pos + 1 < len(operands):
                hi, lo = operands[pos], operands[pos + 1]
                hex_hash = u16_pair_to_hex(hi, lo)
                original = string_table.get(hex_hash, f"<unresolved:{hex_hash}>")
                resolved.append(original)
                pos += 2
            else:
                resolved.append("<missing_string_arg>")
                pos += 1
        elif arg_type == "n":
            if pos < len(operands):
                val = operands[pos]
                if opcode == TrailOpcodes.TRUST_UPDATE and val > 0x7FFF:
                    val = val - 0x10000
                resolved.append(val)
                pos += 1
            else:
                resolved.append(0)
        else:
            resolved.append(operands[pos] if pos < len(operands) else None)
            pos += 1

    return resolved


def operand_names(opcode: TrailOpcodes) -> list[str]:
    """Return canonical argument names for each opcode."""
    _NAMES: dict[TrailOpcodes, list[str]] = {
        TrailOpcodes.GIT_COMMIT:   ["repo", "message"],
        TrailOpcodes.GIT_PUSH:     ["repo"],
        TrailOpcodes.FILE_READ:    ["path"],
        TrailOpcodes.FILE_WRITE:   ["path", "content"],
        TrailOpcodes.FILE_EDIT:    ["path", "old", "new"],
        TrailOpcodes.TEST_RUN:     ["test_path", "expected"],
        TrailOpcodes.SEARCH_CODE:  ["pattern"],
        TrailOpcodes.BOTTLE_DROP:  ["target", "content"],
        TrailOpcodes.BOTTLE_READ:  ["source"],
        TrailOpcodes.LEVEL_UP:     ["level"],
        TrailOpcodes.SPELL_CAST:   ["spell"],
        TrailOpcodes.ROOM_ENTER:   ["room"],
        TrailOpcodes.TRUST_UPDATE: ["target", "delta"],
        TrailOpcodes.CAP_ISSUE:    ["action", "holder"],
        TrailOpcodes.BRANCH:       ["register"],
        TrailOpcodes.NOP:          [],
        TrailOpcodes.COMMENT:      ["comment"],
        TrailOpcodes.LABEL:        ["label"],
    }
    return _NAMES.get(opcode, [])


# ═══════════════════════════════════════════════════════════════════════════════
# TrailExecutor
# ═══════════════════════════════════════════════════════════════════════════════

class TrailExecutor:
    """
    Main execution engine for compiled trail bytecode.

    Features:
      - Step-by-step or full execution
      - Pause/resume at any step
      - Dry-run mode (log without executing)
      - Fail-fast mode (stop on first error)
      - Execution trail generation with SHA-256 fingerprint chain

    Args:
        world: WorldInterface implementation providing actual operations.
        bytecode: Compiled trail bytecode to execute.
        dry_run: If True, log steps without calling WorldInterface methods.
        fail_fast: If True, stop execution on the first failed step.
    """

    def __init__(
        self,
        world: WorldInterface,
        bytecode: bytes,
        dry_run: bool = False,
        fail_fast: bool = False,
    ) -> None:
        self.world = world
        self.bytecode = bytecode
        self.dry_run = dry_run
        self.fail_fast = fail_fast

        decoder = TrailDecoder()
        self.program: TrailProgram = decoder.decode(bytecode)
        self.string_table: dict[str, str] = decoder.string_table

        self._current_index: int = 0
        self._events: list[TrailEvent] = []
        self._paused: bool = False
        self._finished: bool = False
        self._start_time: float = 0.0

    def get_state(self) -> dict[str, Any]:
        """Return current execution state."""
        return {
            "current_index": self._current_index,
            "total_steps": len(self.program.steps),
            "events_count": len(self._events),
            "paused": self._paused,
            "finished": self._finished,
            "dry_run": self.dry_run,
            "fail_fast": self.fail_fast,
        }

    def get_events(self) -> list[TrailEvent]:
        """Return all events recorded so far."""
        return list(self._events)

    def pause(self) -> None:
        """Pause execution."""
        self._paused = True

    def resume(self) -> None:
        """Resume execution after a pause."""
        self._paused = False

    def step(self) -> Optional[TrailEvent]:
        """Execute a single step and return the TrailEvent. Returns None when done."""
        if self._finished or self._paused:
            return None

        while self._current_index < len(self.program.steps):
            trail_step = self.program.steps[self._current_index]
            idx = self._current_index
            self._current_index += 1

            event = self._execute_one(trail_step, idx)
            if event is not None:
                self._events.append(event)
                return event

        self._finished = True
        return None

    def execute(self, resume_from: int = 0) -> TrailResult:
        """
        Execute the full trail from the given step index.

        Args:
            resume_from: Step index to resume from (0 = start from beginning).

        Returns:
            TrailResult with all events, statistics, and execution proof.
        """
        self._start_time = time.monotonic()
        self._current_index = resume_from

        if resume_from > 0:
            self._events = [e for e in self._events if e.step_index < resume_from]
        else:
            self._events = []
            self._finished = False

        while self._current_index < len(self.program.steps):
            if self._paused:
                break

            trail_step = self.program.steps[self._current_index]
            idx = self._current_index
            self._current_index += 1

            event = self._execute_one(trail_step, idx)
            if event is not None:
                self._events.append(event)
                if "ERROR" in event.result and self.fail_fast:
                    self._finished = True
                    break

        self._finished = True
        total_ms = (time.monotonic() - self._start_time) * 1000

        exec_trail = self._build_execution_trail()
        exec_fp = hashlib.sha256(exec_trail).hexdigest()

        failed = sum(1 for e in self._events if "ERROR" in e.result)
        completed = len(self._events) - failed

        return TrailResult(
            success=(failed == 0),
            total_steps=len(self._events),
            completed_steps=completed,
            failed_steps=failed,
            events=list(self._events),
            duration_ms=total_ms,
            execution_trail=exec_trail,
            execution_fingerprint=exec_fp,
        )

    def _execute_one(self, trail_step: TrailStep, index: int) -> Optional[TrailEvent]:
        """Execute a single TrailStep and return a TrailEvent."""
        op = trail_step.opcode
        t_start = time.monotonic()

        if op == TrailOpcodes.TRAIL_BEGIN:
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op,
                operands={"trail_id": trail_step.metadata.get("trail_id", "?")},
                result="trail_begin: execution started",
                duration_ms=duration, timestamp=time.time(),
            )

        if op == TrailOpcodes.TRAIL_END:
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op,
                operands={"status": trail_step.metadata.get("status", 0)},
                result="trail_end: execution finished",
                duration_ms=duration, timestamp=time.time(),
            )

        if op == TrailOpcodes.NOP:
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op, operands={},
                result="nop: skipped", duration_ms=duration, timestamp=time.time(),
            )

        if op in (TrailOpcodes.COMMENT, TrailOpcodes.LABEL):
            resolved = resolve_operands(op, trail_step.operands, self.string_table)
            duration = (time.monotonic() - t_start) * 1000
            names = operand_names(op)
            operands_dict = dict(zip(names, resolved))
            result_str = f"{op.name.lower()}: {resolved[0] if resolved else ''}"
            return TrailEvent(
                step_index=index, opcode=op, operands=operands_dict,
                result=result_str, duration_ms=duration, timestamp=time.time(),
            )

        if op == TrailOpcodes.BRANCH:
            resolved = resolve_operands(op, trail_step.operands, self.string_table)
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op,
                operands={"register": resolved[0] if resolved else 0},
                result="branch: conditional jump",
                duration_ms=duration, timestamp=time.time(),
            )

        resolved = resolve_operands(op, trail_step.operands, self.string_table)
        names = operand_names(op)
        operands_dict = dict(zip(names, resolved))

        if self.dry_run:
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op, operands=operands_dict,
                result=f"DRY-RUN: {op.name}({', '.join(str(r) for r in resolved)})",
                duration_ms=duration, timestamp=time.time(),
            )

        try:
            result_str = self._dispatch(op, resolved)
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op, operands=operands_dict,
                result=result_str, duration_ms=duration, timestamp=time.time(),
            )
        except Exception as e:
            duration = (time.monotonic() - t_start) * 1000
            return TrailEvent(
                step_index=index, opcode=op, operands=operands_dict,
                result=f"{op.name}: ERROR {e}",
                duration_ms=duration, timestamp=time.time(),
            )

    def _dispatch(self, op: TrailOpcodes, args: list[Any]) -> str:
        """Dispatch an opcode to the appropriate WorldInterface method."""
        match op:
            case TrailOpcodes.GIT_COMMIT:
                return self.world.git_commit(str(args[0]), str(args[1]))
            case TrailOpcodes.GIT_PUSH:
                return self.world.git_push(str(args[0]))
            case TrailOpcodes.FILE_READ:
                return self.world.file_read(str(args[0]))
            case TrailOpcodes.FILE_WRITE:
                return self.world.file_write(str(args[0]), str(args[1]))
            case TrailOpcodes.FILE_EDIT:
                return self.world.file_edit(str(args[0]), str(args[1]), str(args[2]))
            case TrailOpcodes.TEST_RUN:
                return self.world.test_run(str(args[0]), int(args[1]))
            case TrailOpcodes.SEARCH_CODE:
                return self.world.search_code(str(args[0]))
            case TrailOpcodes.BOTTLE_DROP:
                return self.world.bottle_drop(str(args[0]), str(args[1]))
            case TrailOpcodes.BOTTLE_READ:
                return self.world.bottle_read(str(args[0]))
            case TrailOpcodes.LEVEL_UP:
                return self.world.level_up("executor", int(args[0]))
            case TrailOpcodes.SPELL_CAST:
                return self.world.spell_cast(str(args[0]))
            case TrailOpcodes.ROOM_ENTER:
                return self.world.room_enter(str(args[0]))
            case TrailOpcodes.TRUST_UPDATE:
                return self.world.trust_update(str(args[0]), float(args[1]))
            case TrailOpcodes.CAP_ISSUE:
                return self.world.cap_issue(str(args[0]), str(args[1]))
            case _:
                return f"{op.name}: no WorldInterface dispatch"

    def _build_execution_trail(self) -> bytes:
        """Build the execution meta-trail recording what the executor did."""
        compiler = TrailCompiler()
        entries: list[dict[str, Any]] = []

        entries.append({
            "op": "TRAIL_BEGIN",
            "agent": "trail-executor",
            "trail_id": "execution-meta-trail",
            "ts": int(time.time()),
            "desc": "Meta-trail: recording what the executor did",
        })

        for ev in self._events:
            op_name = ev.opcode.name
            if ev.opcode == TrailOpcodes.TRAIL_BEGIN:
                entries.append({"op": "COMMENT",
                                "comment": f"exec: trail_begin",
                                "desc": f"Execution started at step {ev.step_index}"})
            elif ev.opcode == TrailOpcodes.TRAIL_END:
                entries.append({"op": "COMMENT",
                                "comment": f"exec: trail_end",
                                "desc": f"Execution finished at step {ev.step_index}"})
            elif ev.opcode == TrailOpcodes.NOP:
                entries.append({"op": "NOP",
                                "desc": f"exec: nop at step {ev.step_index}"})
            elif ev.opcode in (TrailOpcodes.COMMENT, TrailOpcodes.LABEL):
                comment_val = ev.operands.get("comment", ev.operands.get("label", ""))
                entries.append({"op": op_name, "comment": comment_val,
                                "desc": f"exec: {op_name} at step {ev.step_index}"})
            else:
                result_snippet = ev.result[:80]
                entries.append({"op": "COMMENT",
                                "comment": f"exec:{op_name} -> {result_snippet}",
                                "desc": f"Step {ev.step_index}: {ev.result[:60]}"})

        failed = sum(1 for e in self._events if "ERROR" in e.result)
        status = 0 if failed == 0 else 2 if failed < len(self._events) else 1
        entries.append({"op": "TRAIL_END", "steps": len(entries), "status": status,
                        "desc": f"Meta-trail ends: {len(self._events)} steps recorded"})

        program = compiler.compile(entries)
        encoder = TrailEncoder(string_table=dict(compiler.string_table))
        return encoder.encode(program)
