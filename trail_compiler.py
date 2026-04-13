"""
Trail Compiler — converts structured worklog entries into TrailPrograms.

Standalone module extracted from holodeck-studio trail_encoder.py.
Zero external dependencies — uses only Python stdlib.

The compiler is the bridge between an agent's natural worklog format
(a list of dicts) and the compiled bytecode representation. It handles:
  - String hashing for compact operands
  - Timestamp assignment
  - Opcode validation
  - Metadata extraction
  - All 21 TrailOpcodes (17 action + 4 meta)
"""

from __future__ import annotations

import time
from typing import Any

from trail_codec import (
    TrailOpcodes,
    TrailStep,
    TrailProgram,
    TrailEncoder,
    str_to_hash,
)


class TrailCompiler:
    """
    Converts structured worklog entries (dicts) into a TrailProgram.

    Each entry is a dict with at least an 'op' key (opcode name string).
    Other keys depend on the opcode:

      TRAIL_BEGIN: agent, trail_id, ts
      TRAIL_END:   steps, status, desc
      FILE_READ:   path, desc
      FILE_WRITE:  path, content/content_hash, desc
      FILE_EDIT:   path, old/old_hash, new/new_hash, desc
      SEARCH_CODE: pattern, desc
      TEST_RUN:    test_path/path, count/expected_count, desc
      GIT_COMMIT:  repo_id/repo, message/message_hash, desc
      GIT_PUSH:    repo_id/repo, desc
      BOTTLE_DROP: target, content/content_hash, desc
      BOTTLE_READ: source, desc
      LEVEL_UP:    level/new_level, desc
      SPELL_CAST:  spell_id/spell, desc
      ROOM_ENTER:  room_id/room, desc
      TRUST_UPDATE: target, delta, desc
      CAP_ISSUE:   action, holder, desc
      BRANCH:      reg/register, desc
      NOP:         desc
      COMMENT:     comment/text, desc
      LABEL:       label/name, desc
    """

    def __init__(self) -> None:
        self.string_table: dict[str, str] = {}

    def compile(self, entries: list[dict[str, Any]]) -> TrailProgram:
        """
        Compile a list of worklog entries into a TrailProgram.

        Args:
            entries: List of dicts, each with at least 'op' key.

        Returns:
            A TrailProgram ready for encoding.

        Raises:
            ValueError: If an unknown opcode is encountered.
        """
        program = TrailProgram()

        for entry in entries:
            step = self._compile_entry(entry)
            program.add_step(step)

        return program

    def compile_and_encode(self, entries: list[dict[str, Any]]) -> bytes:
        """Compile entries and immediately encode to bytecode."""
        program = self.compile(entries)
        encoder = TrailEncoder(string_table=dict(self.string_table))
        return encoder.encode(program)

    def _compile_entry(self, entry: dict[str, Any]) -> TrailStep:
        """Convert a single worklog entry to a TrailStep."""
        op_name = entry.get("op", "").upper()

        try:
            opcode = TrailOpcodes[op_name]
        except KeyError:
            raise ValueError(f"Unknown opcode: {op_name}")

        compiler_map: dict[TrailOpcodes, Any] = {
            TrailOpcodes.TRAIL_BEGIN: self._compile_trail_begin,
            TrailOpcodes.TRAIL_END: self._compile_trail_end,
            TrailOpcodes.FILE_READ: self._compile_file_read,
            TrailOpcodes.FILE_WRITE: self._compile_file_write,
            TrailOpcodes.FILE_EDIT: self._compile_file_edit,
            TrailOpcodes.SEARCH_CODE: self._compile_search_code,
            TrailOpcodes.TEST_RUN: self._compile_test_run,
            TrailOpcodes.GIT_COMMIT: self._compile_git_commit,
            TrailOpcodes.GIT_PUSH: self._compile_git_push,
            TrailOpcodes.BOTTLE_DROP: self._compile_bottle_drop,
            TrailOpcodes.BOTTLE_READ: self._compile_bottle_read,
            TrailOpcodes.LEVEL_UP: self._compile_level_up,
            TrailOpcodes.SPELL_CAST: self._compile_spell_cast,
            TrailOpcodes.ROOM_ENTER: self._compile_room_enter,
            TrailOpcodes.TRUST_UPDATE: self._compile_trust_update,
            TrailOpcodes.CAP_ISSUE: self._compile_cap_issue,
            TrailOpcodes.BRANCH: self._compile_branch,
            TrailOpcodes.NOP: self._compile_nop,
            TrailOpcodes.COMMENT: self._compile_comment,
            TrailOpcodes.LABEL: self._compile_label,
        }

        compiler_fn = compiler_map.get(opcode)
        if compiler_fn is None:
            raise ValueError(f"No compiler for opcode: {op_name}")
        return compiler_fn(entry)

    def _register(self, s: str) -> tuple[int, int]:
        """Register a string and return its hash as a u16 pair."""
        h = str_to_hash(s)
        self.string_table[h] = s
        hi = int(h[0:4], 16)
        lo = int(h[4:8], 16)
        return (hi, lo)

    # ── Entry Compilers ──────────────────────────────────────────────────────

    def _compile_trail_begin(self, entry: dict) -> TrailStep:
        agent = entry.get("agent", "unknown")
        trail_id = entry.get("trail_id", "untitled")
        ts = entry.get("ts", entry.get("timestamp", int(time.time())))

        self._register(trail_id)
        self._register(agent)
        _, _ = self._register(trail_id)

        agent_id = hash(agent) & 0xFF
        return TrailStep(
            opcode=TrailOpcodes.TRAIL_BEGIN,
            operands=[agent_id],
            metadata={"agent": agent, "trail_id": trail_id, "timestamp": int(ts)},
            timestamp=float(ts),
            description=entry.get("desc", f"Trail begins: {trail_id}"),
        )

    def _compile_trail_end(self, entry: dict) -> TrailStep:
        steps = entry.get("steps", 0)
        status = entry.get("status", 0)
        return TrailStep(
            opcode=TrailOpcodes.TRAIL_END,
            operands=[status],
            metadata={"total_steps": steps, "status": status},
            description=entry.get("desc", "Trail ends"),
        )

    def _compile_file_read(self, entry: dict) -> TrailStep:
        path = entry.get("path", "")
        hi, lo = self._register(path)
        return TrailStep(
            opcode=TrailOpcodes.FILE_READ,
            operands=[hi, lo],
            description=entry.get("desc", f"Read: {path}"),
        )

    def _compile_file_write(self, entry: dict) -> TrailStep:
        path = entry.get("path", "")
        content = entry.get("content", entry.get("content_hash", ""))
        hi1, lo1 = self._register(path)
        hi2, lo2 = self._register(content)
        return TrailStep(
            opcode=TrailOpcodes.FILE_WRITE,
            operands=[hi1, lo1, hi2, lo2],
            description=entry.get("desc", f"Write: {path}"),
        )

    def _compile_file_edit(self, entry: dict) -> TrailStep:
        path = entry.get("path", "")
        old_content = entry.get("old", entry.get("old_hash", ""))
        new_content = entry.get("new", entry.get("new_hash", ""))
        hi1, lo1 = self._register(path)
        hi2, lo2 = self._register(old_content)
        hi3, lo3 = self._register(new_content)
        return TrailStep(
            opcode=TrailOpcodes.FILE_EDIT,
            operands=[hi1, lo1, hi2, lo2, hi3, lo3],
            description=entry.get("desc", f"Edit: {path}"),
        )

    def _compile_search_code(self, entry: dict) -> TrailStep:
        pattern = entry.get("pattern", "")
        hi, lo = self._register(pattern)
        return TrailStep(
            opcode=TrailOpcodes.SEARCH_CODE,
            operands=[hi, lo],
            description=entry.get("desc", f"Search: {pattern}"),
        )

    def _compile_test_run(self, entry: dict) -> TrailStep:
        test_path = entry.get("test_path", entry.get("path", ""))
        count = entry.get("count", entry.get("expected_count", 0))
        hi, lo = self._register(test_path)
        return TrailStep(
            opcode=TrailOpcodes.TEST_RUN,
            operands=[hi, lo, count],
            description=entry.get("desc", f"Test: {test_path} ({count} tests)"),
        )

    def _compile_git_commit(self, entry: dict) -> TrailStep:
        repo_id = entry.get("repo_id", entry.get("repo", 0))
        message = entry.get("message", entry.get("message_hash", ""))
        hi, lo = self._register(str(message))
        return TrailStep(
            opcode=TrailOpcodes.GIT_COMMIT,
            operands=[int(repo_id) & 0xFFFF, hi, lo],
            description=entry.get("desc", f"Git commit: repo={repo_id}"),
        )

    def _compile_git_push(self, entry: dict) -> TrailStep:
        repo_id = entry.get("repo_id", entry.get("repo", 0))
        return TrailStep(
            opcode=TrailOpcodes.GIT_PUSH,
            operands=[int(repo_id) & 0xFFFF],
            description=entry.get("desc", f"Git push: repo={repo_id}"),
        )

    def _compile_bottle_drop(self, entry: dict) -> TrailStep:
        target = entry.get("target", "")
        content = entry.get("content", entry.get("content_hash", ""))
        hi1, lo1 = self._register(target)
        hi2, lo2 = self._register(content)
        return TrailStep(
            opcode=TrailOpcodes.BOTTLE_DROP,
            operands=[hi1, lo1, hi2, lo2],
            description=entry.get("desc", f"Bottle->{target}"),
        )

    def _compile_bottle_read(self, entry: dict) -> TrailStep:
        source = entry.get("source", "")
        hi, lo = self._register(source)
        return TrailStep(
            opcode=TrailOpcodes.BOTTLE_READ,
            operands=[hi, lo],
            description=entry.get("desc", f"Bottle<-{source}"),
        )

    def _compile_level_up(self, entry: dict) -> TrailStep:
        level = entry.get("level", entry.get("new_level", 0))
        return TrailStep(
            opcode=TrailOpcodes.LEVEL_UP,
            operands=[int(level) & 0xFFFF],
            description=entry.get("desc", f"Level up: {level}"),
        )

    def _compile_spell_cast(self, entry: dict) -> TrailStep:
        spell_id = entry.get("spell_id", entry.get("spell", ""))
        hi, lo = self._register(str(spell_id))
        return TrailStep(
            opcode=TrailOpcodes.SPELL_CAST,
            operands=[hi, lo],
            description=entry.get("desc", f"Cast: {spell_id}"),
        )

    def _compile_room_enter(self, entry: dict) -> TrailStep:
        room_id = entry.get("room_id", entry.get("room", ""))
        hi, lo = self._register(str(room_id))
        return TrailStep(
            opcode=TrailOpcodes.ROOM_ENTER,
            operands=[hi, lo],
            description=entry.get("desc", f"Enter: {room_id}"),
        )

    def _compile_trust_update(self, entry: dict) -> TrailStep:
        target = entry.get("target", "")
        delta = entry.get("delta", 0)
        hi, lo = self._register(target)
        return TrailStep(
            opcode=TrailOpcodes.TRUST_UPDATE,
            operands=[hi, lo, int(delta) & 0xFFFF],
            description=entry.get("desc", f"Trust: {target} {delta:+d}"),
        )

    def _compile_cap_issue(self, entry: dict) -> TrailStep:
        action = entry.get("action", "")
        holder = entry.get("holder", "")
        hi1, lo1 = self._register(action)
        hi2, lo2 = self._register(holder)
        return TrailStep(
            opcode=TrailOpcodes.CAP_ISSUE,
            operands=[hi1, lo1, hi2, lo2],
            description=entry.get("desc", f"Cap: {action}->{holder}"),
        )

    def _compile_branch(self, entry: dict) -> TrailStep:
        reg = entry.get("reg", entry.get("register", 0))
        return TrailStep(
            opcode=TrailOpcodes.BRANCH,
            operands=[int(reg) & 0xFFFF],
            description=entry.get("desc", f"Branch on R{reg}"),
        )

    def _compile_nop(self, entry: dict) -> TrailStep:
        return TrailStep(
            opcode=TrailOpcodes.NOP,
            description=entry.get("desc", "NOP"),
        )

    def _compile_comment(self, entry: dict) -> TrailStep:
        comment = entry.get("comment", entry.get("text", ""))
        hi, lo = self._register(comment)
        return TrailStep(
            opcode=TrailOpcodes.COMMENT,
            operands=[hi, lo],
            description=entry.get("desc", f"; {comment}"),
        )

    def _compile_label(self, entry: dict) -> TrailStep:
        label = entry.get("label", entry.get("name", ""))
        hi, lo = self._register(label)
        return TrailStep(
            opcode=TrailOpcodes.LABEL,
            operands=[hi, lo],
            description=entry.get("desc", f":{label}"),
        )
