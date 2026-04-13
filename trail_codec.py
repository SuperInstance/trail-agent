"""
Trail Codec — Core trail encoding, decoding, printing, and verification.

Standalone module extracted from holodeck-studio trail_encoder.py.
Zero external dependencies — uses only Python stdlib.

Design Philosophy:
    "The trail IS the code." — Oracle1's Nudge

    An agent's worklog is a *program* that, if executed in order, reproduces
    the agent's journey through a codebase. This module encodes that journey
    as compact bytecode: replayable, verifiable, composable, and auditable.

Bytecode Format:
    [0xA0] [agent_id: u8] [trail_id: 4 bytes] [timestamp: 4 bytes]  -- TRAIL_BEGIN
    [opcode: u8] [operand_count: u8] [operands: variable u16...]     -- each step
    [0xA1] [total_steps: u16] [status: u8]                           -- TRAIL_END
    [0xB0] [table_length: u16]
      [hash: 8 bytes] [string_length: u8] [string_bytes: variable]
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Trail Opcodes
# ═══════════════════════════════════════════════════════════════════════════════

class TrailOpcodes(IntEnum):
    """
    Trail-FLUX opcodes. Range 0x90-0xFF.

    Organized into:
      - Trail Operations (0x90-0x9F): High-level fleet actions
      - Meta Operations   (0xA0-0xA3): Trail framing and annotations
      - Hash Table marker (0xB0):      String recovery section
    """
    # Trail Operations — high-level fleet actions
    GIT_COMMIT   = 0x90   # 2 args: repo_id, message_hash
    GIT_PUSH     = 0x91   # 1 arg:  repo_id
    FILE_READ    = 0x92   # 1 arg:  path_hash
    FILE_WRITE   = 0x93   # 2 args: path_hash, content_hash
    FILE_EDIT    = 0x94   # 3 args: path_hash, old_hash, new_hash
    TEST_RUN     = 0x95   # 2 args: test_path, expected_count
    SEARCH_CODE  = 0x96   # 1 arg:  pattern_hash
    BOTTLE_DROP  = 0x97   # 2 args: target, content_hash
    BOTTLE_READ  = 0x98   # 1 arg:  source
    LEVEL_UP     = 0x99   # 1 arg:  new_level
    SPELL_CAST   = 0x9A   # 1 arg:  spell_id
    ROOM_ENTER   = 0x9B   # 1 arg:  room_id
    TRUST_UPDATE = 0x9C   # 2 args: target, delta
    CAP_ISSUE    = 0x9D   # 2 args: action, holder
    BRANCH       = 0x9E   # 1 arg:  condition_register (JNZ for trails)
    NOP          = 0x9F   # 0 args: trail marker / padding

    # Meta Operations — trail framing and annotations
    TRAIL_BEGIN  = 0xA0   # args: agent_name, trail_id, timestamp
    TRAIL_END    = 0xA1   # args: total_steps, status
    COMMENT      = 0xA2   # 1 arg:  comment_hash
    LABEL        = 0xA3   # 1 arg:  label_hash

    # Hash Table marker
    HASHTABLE    = 0xB0   # start of string hash table section

    @classmethod
    def is_valid(cls, value: int) -> bool:
        """Check if a byte value is a valid Trail-FLUX opcode."""
        return value in cls._value2member_map_

    @classmethod
    def is_trail_op(cls, value: int) -> bool:
        """Check if opcode is a trail action (0x90-0x9F)."""
        return 0x90 <= value <= 0x9F

    @classmethod
    def is_meta_op(cls, value: int) -> bool:
        """Check if opcode is a meta/structural operation (0xA0-0xA3)."""
        return 0xA0 <= value <= 0xA3


# ═══════════════════════════════════════════════════════════════════════════════
# Operand Signatures
# ═══════════════════════════════════════════════════════════════════════════════

OPCODE_OPERAND_COUNT: dict[TrailOpcodes, int] = {
    TrailOpcodes.GIT_COMMIT:   2,
    TrailOpcodes.GIT_PUSH:     1,
    TrailOpcodes.FILE_READ:    1,
    TrailOpcodes.FILE_WRITE:   2,
    TrailOpcodes.FILE_EDIT:    3,
    TrailOpcodes.TEST_RUN:     2,
    TrailOpcodes.SEARCH_CODE:  1,
    TrailOpcodes.BOTTLE_DROP:  2,
    TrailOpcodes.BOTTLE_READ:  1,
    TrailOpcodes.LEVEL_UP:     1,
    TrailOpcodes.SPELL_CAST:   1,
    TrailOpcodes.ROOM_ENTER:   1,
    TrailOpcodes.TRUST_UPDATE: 2,
    TrailOpcodes.CAP_ISSUE:    2,
    TrailOpcodes.BRANCH:       1,
    TrailOpcodes.NOP:          0,
    TrailOpcodes.COMMENT:      1,
    TrailOpcodes.LABEL:        1,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrailStep:
    """
    A single step in an agent's trail.

    Attributes:
        opcode:      The TrailOpcodes value for this step.
        operands:    List of u16 operand values (hash references or numeric IDs).
        metadata:    Optional dict of extra data (not encoded in bytecode).
        timestamp:   Unix timestamp when this step occurred.
        description: Human-readable description of this step.
    """
    opcode: TrailOpcodes
    operands: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    description: str = ""

    def __post_init__(self) -> None:
        """Validate and clamp operands on creation."""
        if not isinstance(self.opcode, TrailOpcodes):
            self.opcode = TrailOpcodes(self.opcode)
        self.operands = [int(op) & 0xFFFF for op in self.operands]


@dataclass
class TrailProgram:
    """
    A complete trail — an ordered sequence of TrailSteps forming a compilable
    program that reproduces an agent's journey.

    The trail must start with TRAIL_BEGIN and end with TRAIL_END.
    """
    steps: list[TrailStep] = field(default_factory=list)

    def add_step(self, step: TrailStep) -> TrailProgram:
        """Append a step and return self for chaining."""
        self.steps.append(step)
        return self

    @property
    def is_valid(self) -> bool:
        """Check if trail has proper begin/end markers."""
        if len(self.steps) < 2:
            return False
        return (self.steps[0].opcode == TrailOpcodes.TRAIL_BEGIN
                and self.steps[-1].opcode == TrailOpcodes.TRAIL_END)

    @property
    def action_steps(self) -> list[TrailStep]:
        """Return only the action steps (excluding TRAIL_BEGIN, TRAIL_END, NOP)."""
        skip = {TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END, TrailOpcodes.NOP}
        return [s for s in self.steps if s.opcode not in skip]

    def concatenate(self, other: TrailProgram) -> TrailProgram:
        """
        Concatenate another trail onto this one.
        Removes the TRAIL_END of self and TRAIL_BEGIN of other,
        producing a seamless merged trail.
        """
        if not self.is_valid or not other.is_valid:
            raise ValueError("Both trails must be valid to concatenate")
        merged = TrailProgram(steps=self.steps[:-1] + other.steps[1:])
        return merged

    def fingerprint(self) -> str:
        """
        Compute a SHA-256 fingerprint of this trail program.
        Uses the compiled bytecode to ensure byte-level determinism.
        Returns the full 64-char hex digest.
        """
        bytecode = TrailEncoder().encode(self)
        return hashlib.sha256(bytecode).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# Hash Utilities
# ═══════════════════════════════════════════════════════════════════════════════

def str_to_hash(s: str) -> str:
    """
    Hash a string to an 8-char hex digest (first 4 bytes of SHA-256).
    The 4 bytes are split into two u16 values for binary encoding.
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def str_hash_to_u16_pair(s: str) -> tuple[int, int]:
    """
    Convert a string's hash to a pair of u16 values for bytecode encoding.
    first 4 hex chars -> high, next 4 hex chars -> low.
    """
    h = str_to_hash(s)
    high = int(h[0:4], 16)
    low = int(h[4:8], 16)
    return (high, low)


def u16_pair_to_hex(hi: int, lo: int) -> str:
    """Convert a u16 pair back to an 8-char hex string."""
    return f"{hi & 0xFFFF:04x}{lo & 0xFFFF:04x}"


@dataclass
class HashTableEntry:
    """A single entry in the string hash table."""
    hash_hex: str
    original: str


# ═══════════════════════════════════════════════════════════════════════════════
# Encoder
# ═══════════════════════════════════════════════════════════════════════════════

class TrailEncoder:
    """
    Converts a TrailProgram to compact FLUX bytecode.

    Encoding format:
        TRAIL_BEGIN: [0xA0] [agent_id: u8] [trail_id: u16 u16] [timestamp: u16 u16]
        Each step:   [opcode: u8] [operand_count: u8] [operands: u16 each]
        TRAIL_END:   [0xA1] [total_steps: u16] [status: u8]
        HASH TABLE:  [0xB0] [entry_count: u16]
                     [hash_hi: u16] [hash_lo: u16] [strlen: u8] [string_bytes]
    """

    def __init__(self, string_table: dict[str, str] | None = None) -> None:
        """Initialize encoder with optional pre-populated string table."""
        self.string_table: dict[str, str] = string_table or {}

    def _register_string(self, s: str) -> str:
        """Register a string in the hash table and return its hash."""
        h = str_to_hash(s)
        if h not in self.string_table:
            self.string_table[h] = s
        return h

    def encode(self, program: TrailProgram) -> bytes:
        """Encode a TrailProgram to compact bytecode bytes."""
        if not program.steps:
            raise ValueError("Cannot encode empty trail program")

        buf = bytearray()

        for step in program.steps:
            op = step.opcode

            if op == TrailOpcodes.TRAIL_BEGIN:
                buf.extend(self._encode_trail_begin(step))
            elif op == TrailOpcodes.TRAIL_END:
                buf.extend(self._encode_trail_end(step, len(program.steps)))
            elif op == TrailOpcodes.NOP:
                buf.append(int(op))
            else:
                buf.append(int(op))
                buf.append(len(step.operands))
                for operand in step.operands:
                    buf.extend(struct.pack("<H", int(operand) & 0xFFFF))

        buf.extend(self._encode_hash_table())
        return bytes(buf)

    def _encode_trail_begin(self, step: TrailStep) -> bytes:
        """Encode TRAIL_BEGIN: [0xA0] [agent_id:u8] [trail_id:4B] [timestamp:4B]."""
        buf = bytearray()
        buf.append(int(TrailOpcodes.TRAIL_BEGIN))

        agent = step.metadata.get("agent", "unknown")
        agent_id = step.operands[0] if len(step.operands) > 0 else (hash(agent) & 0xFF)
        buf.append(agent_id & 0xFF)

        trail_id = step.metadata.get("trail_id", "")
        if trail_id:
            hi, lo = str_hash_to_u16_pair(trail_id)
            buf.extend(struct.pack("<H", hi))
            buf.extend(struct.pack("<H", lo))
        else:
            buf.extend(struct.pack("<H", 0))
            buf.extend(struct.pack("<H", 0))

        ts = step.metadata.get("timestamp", int(time.time()))
        if step.timestamp > 0:
            ts = int(step.timestamp)
        buf.extend(struct.pack("<H", (ts >> 16) & 0xFFFF))
        buf.extend(struct.pack("<H", ts & 0xFFFF))

        return bytes(buf)

    def _encode_trail_end(self, step: TrailStep, total_steps: int) -> bytes:
        """Encode TRAIL_END: [0xA1] [total_steps:u16] [status:u8]."""
        buf = bytearray()
        buf.append(int(TrailOpcodes.TRAIL_END))
        buf.extend(struct.pack("<H", total_steps))
        status = step.operands[0] if len(step.operands) > 0 else 0
        buf.append(status & 0xFF)
        return bytes(buf)

    def _encode_hash_table(self) -> bytes:
        """Encode the string hash table section."""
        buf = bytearray()
        buf.append(int(TrailOpcodes.HASHTABLE))

        entries = sorted(self.string_table.items())
        buf.extend(struct.pack("<H", len(entries)))

        for hash_hex, original in entries:
            hi = int(hash_hex[0:4], 16)
            lo = int(hash_hex[4:8], 16)
            buf.extend(struct.pack("<H", hi))
            buf.extend(struct.pack("<H", lo))

            encoded = original.encode("utf-8")
            buf.append(len(encoded) & 0xFF)
            buf.extend(encoded)

        return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════════
# Decoder
# ═══════════════════════════════════════════════════════════════════════════════

class TrailDecoder:
    """
    Converts FLUX bytecode back into a TrailStep sequence.

    This is the inverse operation of TrailEncoder.
    """

    def __init__(self) -> None:
        self.string_table: dict[str, str] = {}
        self._pos: int = 0
        self._data: bytes = b""

    def decode(self, bytecode: bytes) -> TrailProgram:
        """Decode bytecode into a TrailProgram."""
        self._data = bytecode
        self._pos = 0
        self.string_table = {}
        steps: list[TrailStep] = []

        while self._pos < len(self._data):
            saved_pos = self._pos
            op_byte = self._read_u8()

            if op_byte == int(TrailOpcodes.HASHTABLE):
                self._decode_hash_table()
                break

            if not TrailOpcodes.is_valid(op_byte):
                raise ValueError(
                    f"Invalid opcode 0x{op_byte:02X} at offset {saved_pos}"
                )

            op = TrailOpcodes(op_byte)

            if op == TrailOpcodes.TRAIL_BEGIN:
                step = self._decode_trail_begin()
            elif op == TrailOpcodes.TRAIL_END:
                step = self._decode_trail_end()
            elif op == TrailOpcodes.NOP:
                step = TrailStep(opcode=op)
            else:
                step = self._decode_general_step(op)

            steps.append(step)

        return TrailProgram(steps=steps)

    def _read_u8(self) -> int:
        """Read a single byte from the data stream."""
        v = self._data[self._pos]
        self._pos += 1
        return v

    def _read_u16(self) -> int:
        """Read a little-endian u16 from the data stream."""
        lo = self._data[self._pos]
        hi = self._data[self._pos + 1]
        self._pos += 2
        return lo | (hi << 8)

    def _decode_trail_begin(self) -> TrailStep:
        """Decode a TRAIL_BEGIN instruction."""
        agent_id = self._read_u8()
        trail_id_hi = self._read_u16()
        trail_id_lo = self._read_u16()
        ts_hi = self._read_u16()
        ts_lo = self._read_u16()

        ts = (ts_hi << 16) | ts_lo
        trail_id_hex = f"{trail_id_hi:04x}{trail_id_lo:04x}"
        trail_id_str = self.string_table.get(trail_id_hex, trail_id_hex)

        return TrailStep(
            opcode=TrailOpcodes.TRAIL_BEGIN,
            operands=[agent_id],
            metadata={
                "agent_id": agent_id,
                "trail_id_hex": trail_id_hex,
                "trail_id": trail_id_str,
                "timestamp": ts,
            },
            timestamp=float(ts),
            description="Trail begins",
        )

    def _decode_trail_end(self) -> TrailStep:
        """Decode a TRAIL_END instruction."""
        total_steps = self._read_u16()
        status = self._read_u8()

        status_msg = {0: "success", 1: "error", 2: "partial", 3: "cancelled"}
        return TrailStep(
            opcode=TrailOpcodes.TRAIL_END,
            operands=[status],
            metadata={"total_steps": total_steps, "status": status},
            description=f"Trail ends ({status_msg.get(status, 'unknown')})",
        )

    def _decode_general_step(self, op: TrailOpcodes) -> TrailStep:
        """Decode a general instruction with operands."""
        operand_count = self._read_u8()
        operands = [self._read_u16() for _ in range(operand_count)]
        return TrailStep(opcode=op, operands=operands)

    def _decode_hash_table(self) -> None:
        """Read and populate the string hash table."""
        entry_count = self._read_u16()
        for _ in range(entry_count):
            hi = self._read_u16()
            lo = self._read_u16()
            hash_hex = f"{hi:04x}{lo:04x}"
            str_len = self._read_u8()
            string_bytes = self._data[self._pos:self._pos + str_len]
            self._pos += str_len
            self.string_table[hash_hex] = string_bytes.decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Printer
# ═══════════════════════════════════════════════════════════════════════════════

class TrailPrinter:
    """
    Pretty-prints trail bytecode as human-readable operations.

    Output formats:
      - 'text':    Plain text listing (default)
      - 'hex':     Include hex offsets
      - 'verbose': Include hash table lookups
      - 'compact': One line per step, minimal formatting
    """

    def __init__(self, string_table: dict[str, str] | None = None) -> None:
        self.string_table = string_table or {}

    def print_program(self, program: TrailProgram, fmt: str = "text") -> str:
        """Print a TrailProgram in the specified format."""
        return self._render_steps(program.steps, fmt)

    def print_bytecode(self, bytecode: bytes, fmt: str = "text") -> str:
        """Decode bytecode and print in the specified format."""
        decoder = TrailDecoder()
        decoder.decode(bytecode)
        self.string_table = decoder.string_table
        program = decoder.decode(bytecode)
        return self._render_steps(program.steps, fmt)

    def _render_steps(self, steps: list[TrailStep], fmt: str) -> str:
        """Render steps into a formatted string."""
        lines: list[str] = []

        if fmt == "text":
            lines.append("=== TRAIL-FLUX DISASSEMBLY ===")
            lines.append("")
            for i, step in enumerate(steps):
                lines.append(self._format_step_text(step, i))
            lines.append("")
            lines.append("=== END OF TRAIL ===")

        elif fmt == "hex":
            lines.append("=== TRAIL-FLUX HEX DUMP ===")
            lines.append("")
            offset = 0
            for i, step in enumerate(steps):
                step_bytes = self._estimate_step_size(step)
                lines.append(f"  {offset:04X}: {self._format_step_text(step, i)}")
                offset += step_bytes
            lines.append("")
            lines.append("=== END OF TRAIL ===")

        elif fmt == "verbose":
            lines.append("=== TRAIL-FLUX VERBOSE ===")
            lines.append("")
            for i, step in enumerate(steps):
                lines.append(f"  [{i:03d}] {self._format_step_verbose(step)}")
                lines.append(f"        opcode: 0x{int(step.opcode):02X} ({step.opcode.name})")
                lines.append(f"        operands: {step.operands}")
                if step.description:
                    lines.append(f"        desc: {step.description}")
                if step.timestamp:
                    lines.append(f"        timestamp: {step.timestamp}")
                lines.append("")
            if self.string_table:
                lines.append("  --- STRING TABLE ---")
                for h, s in sorted(self.string_table.items()):
                    lines.append(f'    {h} -> "{s}"')
            lines.append("")
            lines.append("=== END OF TRAIL ===")

        elif fmt == "compact":
            for step in steps:
                lines.append(self._format_step_compact(step))

        else:
            raise ValueError(f"Unknown format: {fmt}")

        return "\n".join(lines)

    def _format_step_text(self, step: TrailStep, index: int) -> str:
        """Format a step for text output."""
        op_name = step.opcode.name

        if step.opcode == TrailOpcodes.TRAIL_BEGIN:
            agent = step.metadata.get("trail_id", "?")
            ts = step.metadata.get("timestamp", 0)
            return f"  TRAIL_BEGIN  agent={agent}  ts={ts}"

        if step.opcode == TrailOpcodes.TRAIL_END:
            total = step.metadata.get("total_steps", "?")
            status = step.metadata.get("status", "?")
            return f"  TRAIL_END    steps={total}  status={status}"

        if step.opcode == TrailOpcodes.NOP:
            return "  NOP"

        resolved = self._resolve_operands(step.operands)
        op_str = f"{op_name}"
        if resolved:
            op_str += f"  {', '.join(str(r) for r in resolved)}"
        desc = f"  ; {step.description}" if step.description else ""
        return f"  {op_str}{desc}"

    def _format_step_verbose(self, step: TrailStep) -> str:
        """Format a step for verbose output."""
        op_name = step.opcode.name.ljust(14)

        if step.opcode == TrailOpcodes.TRAIL_BEGIN:
            return f"  [{op_name}] trail_id={step.metadata.get('trail_id', '?')}"

        if step.opcode == TrailOpcodes.TRAIL_END:
            return f"  [{op_name}] total={step.metadata.get('total_steps', '?')}"

        resolved = self._resolve_operands(step.operands)
        return f"  [{op_name}] args={resolved}"

    def _format_step_compact(self, step: TrailStep) -> str:
        """Format a step in compact one-line form."""
        op_name = step.opcode.name
        ops = ",".join(str(o) for o in step.operands)
        if ops:
            return f"{op_name} {ops}"
        return op_name

    def _resolve_operands(self, operands: list[int]) -> list[str]:
        """Try to resolve operand pairs back to hash strings."""
        result: list[str] = []
        i = 0
        while i < len(operands):
            if i + 1 < len(operands):
                hex_str = u16_pair_to_hex(operands[i], operands[i + 1])
                original = self.string_table.get(hex_str)
                if original:
                    result.append(f'"{original}"')
                    i += 2
                    continue
            result.append(str(operands[i]))
            i += 1
        return result

    def _estimate_step_size(self, step: TrailStep) -> int:
        """Estimate byte size of a step for hex dump offsets."""
        if step.opcode == TrailOpcodes.TRAIL_BEGIN:
            return 11
        if step.opcode == TrailOpcodes.TRAIL_END:
            return 4
        if step.opcode == TrailOpcodes.NOP:
            return 1
        return 2 + len(step.operands) * 2


# ═══════════════════════════════════════════════════════════════════════════════
# Verifier
# ═══════════════════════════════════════════════════════════════════════════════

class TrailVerifier:
    """
    Verifies trail program integrity.

    Six verification checks:
      1. Structural: valid TRAIL_BEGIN / TRAIL_END framing
      2. Opcode: all opcodes are valid Trail-FLUX opcodes
      3. Operand count: each step has the expected number of operands
      4. Round-trip: encode->decode produces identical steps
      5. Fingerprint: same trail always produces the same hash
      6. Hash table: all referenced hashes exist in the table
    """

    def __init__(self, string_table: dict[str, str] | None = None) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.string_table: dict[str, str] = string_table or {}

    def verify(self, program: TrailProgram) -> bool:
        """Run all verification checks. Returns True if all pass."""
        self.errors = []
        self.warnings = []

        self._check_structure(program)
        self._check_opcodes(program)
        self._check_operands(program)
        self._check_roundtrip(program)
        self._check_fingerprint(program)
        self._check_hash_table(program)

        return len(self.errors) == 0

    def verify_bytecode(self, bytecode: bytes) -> bool:
        """Verify bytecode by decoding and then running full verification."""
        try:
            decoder = TrailDecoder()
            program = decoder.decode(bytecode)
            return self.verify(program)
        except Exception as e:
            self.errors.append(f"Bytecode decode error: {e}")
            return False

    def report(self) -> str:
        """Generate a human-readable verification report."""
        lines: list[str] = []
        if not self.errors and not self.warnings:
            lines.append("[PASS] Trail verification PASSED -- all checks clean")
        else:
            if self.warnings:
                lines.append(f"[WARN] {len(self.warnings)} warning(s):")
                for w in self.warnings:
                    lines.append(f"  - {w}")
            if self.errors:
                lines.append(f"[FAIL] {len(self.errors)} error(s):")
                for e in self.errors:
                    lines.append(f"  - {e}")
        return "\n".join(lines)

    def _check_structure(self, program: TrailProgram) -> None:
        """Check 1: TRAIL_BEGIN / TRAIL_END framing."""
        if len(program.steps) < 2:
            self.errors.append(
                f"Trail too short: {len(program.steps)} steps (minimum 2)"
            )
            return

        first = program.steps[0]
        last = program.steps[-1]

        if first.opcode != TrailOpcodes.TRAIL_BEGIN:
            self.errors.append(
                f"Trail must start with TRAIL_BEGIN, got {first.opcode.name}"
            )
        if last.opcode != TrailOpcodes.TRAIL_END:
            self.errors.append(
                f"Trail must end with TRAIL_END, got {last.opcode.name}"
            )

    def _check_opcodes(self, program: TrailProgram) -> None:
        """Check 2: all opcodes are valid Trail-FLUX opcodes."""
        for i, step in enumerate(program.steps):
            if not isinstance(step.opcode, TrailOpcodes):
                self.errors.append(
                    f"Step {i}: invalid opcode type {type(step.opcode)}"
                )

    # Operand types: 's' = string (u16 pair), 'n' = numeric (single u16)
    _OPERAND_TYPES: dict[TrailOpcodes, list[str]] = {
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

    def _check_operands(self, program: TrailProgram) -> None:
        """Check 3: operand counts match expected signatures."""
        for i, step in enumerate(program.steps):
            if step.opcode in (TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END,
                               TrailOpcodes.NOP):
                continue
            arg_types = self._OPERAND_TYPES.get(step.opcode)
            if arg_types is not None:
                # Count expected u16 slots: 's' = 2, 'n' = 1
                expected = sum(2 if t == "s" else 1 for t in arg_types)
                actual = len(step.operands)
                if actual != expected:
                    self.errors.append(
                        f"Step {i} ({step.opcode.name}): expected {expected} "
                        f"operands, got {actual}"
                    )

    def _check_roundtrip(self, program: TrailProgram) -> None:
        """Check 4: encode->decode produces identical steps.

        Note: TRAIL_BEGIN and TRAIL_END have special encoding where operands
        are derived from metadata, so their operands may differ after round-trip.
        Only opcode identity is checked for these special opcodes.
        """
        try:
            encoder = TrailEncoder(string_table=dict(self.string_table))
            bytecode = encoder.encode(program)
            decoder = TrailDecoder()
            decoded = decoder.decode(bytecode)

            if len(decoded.steps) != len(program.steps):
                self.errors.append(
                    f"Round-trip step count mismatch: "
                    f"{len(program.steps)} -> {len(decoded.steps)}"
                )
                return

            special_ops = {TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END}
            for i, (orig, dec) in enumerate(zip(program.steps, decoded.steps)):
                if orig.opcode != dec.opcode:
                    self.errors.append(
                        f"Round-trip opcode mismatch at step {i}: "
                        f"{orig.opcode.name} != {dec.opcode.name}"
                    )
                    return
                if orig.opcode not in special_ops:
                    if orig.operands != dec.operands:
                        self.errors.append(
                            f"Round-trip operand mismatch at step {i} "
                            f"({orig.opcode.name})"
                        )
                        return
        except Exception as e:
            self.errors.append(f"Round-trip check failed: {e}")

    def _check_fingerprint(self, program: TrailProgram) -> None:
        """Check 5: same trail always produces the same hash."""
        try:
            fp1 = program.fingerprint()
            fp2 = program.fingerprint()
            if fp1 != fp2:
                self.errors.append(
                    f"Fingerprint non-deterministic: {fp1} != {fp2}"
                )
        except Exception as e:
            self.warnings.append(f"Fingerprint check skipped: {e}")

    def _check_hash_table(self, program: TrailProgram) -> None:
        """Check 6: all referenced string hashes exist in the table.
        Skipped when no string table is provided (not an error condition).
        """
        if not self.string_table:
            return

        for i, step in enumerate(program.steps):
            if step.opcode in (TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END,
                               TrailOpcodes.NOP):
                continue
            for j in range(0, len(step.operands) - 1, 2):
                hi, lo = step.operands[j], step.operands[j + 1]
                hex_hash = u16_pair_to_hex(hi, lo)
                if hex_hash not in self.string_table:
                    # Might be a numeric operand pair, not a string hash
                    # Only warn if it looks like a hash (non-trivial values)
                    if hi != 0 or lo != 0:
                        self.warnings.append(
                            f"Step {i} ({step.opcode.name}): hash {hex_hash} "
                            f"not in string table"
                        )
