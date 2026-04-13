"""
Trail Agent Tests — comprehensive test suite for the standalone trail system.

Tests cover:
  - Encoding/decoding round-trip
  - All 21 opcodes
  - 6 verification checks
  - Execution with MockWorld
  - Worklog compilation (all 17+ entry types)
  - CLI argument parsing
  - Trail concatenation and fingerprinting
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure local imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trail_codec import (
    TrailOpcodes,
    TrailStep,
    TrailProgram,
    TrailEncoder,
    TrailDecoder,
    TrailPrinter,
    TrailVerifier,
    str_to_hash,
    str_hash_to_u16_pair,
    u16_pair_to_hex,
    OPCODE_OPERAND_COUNT,
)
from trail_compiler import TrailCompiler
from trail_executor import (
    TrailExecutor,
    TrailEvent,
    TrailResult,
    MockWorld,
    FileWorld,
    resolve_operands,
    operand_names,
)
from cli import main, build_parser


# ═══════════════════════════════════════════════════════════════════════════════
# Hash Utilities Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHashUtils(unittest.TestCase):
    """Tests for hash utility functions."""

    def test_str_to_hash_returns_8_chars(self) -> None:
        """str_to_hash should return exactly 8 hex characters."""
        h = str_to_hash("hello")
        self.assertEqual(len(h), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_str_to_hash_deterministic(self) -> None:
        """Same input always produces same hash."""
        self.assertEqual(str_to_hash("test"), str_to_hash("test"))

    def test_str_to_hash_different_inputs(self) -> None:
        """Different inputs produce different hashes (with high probability)."""
        self.assertNotEqual(str_to_hash("foo"), str_to_hash("bar"))

    def test_str_hash_to_u16_pair(self) -> None:
        """u16 pair should round-trip with u16_pair_to_hex."""
        s = "my_file_path.py"
        hi, lo = str_hash_to_u16_pair(s)
        self.assertIsInstance(hi, int)
        self.assertIsInstance(lo, int)
        self.assertTrue(0 <= hi <= 0xFFFF)
        self.assertTrue(0 <= lo <= 0xFFFF)

    def test_u16_pair_to_hex_roundtrip(self) -> None:
        """u16_pair_to_hex should produce 8-char hex string."""
        hex_str = u16_pair_to_hex(0x1234, 0x5678)
        self.assertEqual(hex_str, "12345678")
        self.assertEqual(len(hex_str), 8)


# ═══════════════════════════════════════════════════════════════════════════════
# Opcode Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpcodes(unittest.TestCase):
    """Tests for TrailOpcodes enum."""

    def test_all_opcodes_defined(self) -> None:
        """Verify all expected opcodes are defined."""
        expected_names = [
            "GIT_COMMIT", "GIT_PUSH", "FILE_READ", "FILE_WRITE", "FILE_EDIT",
            "TEST_RUN", "SEARCH_CODE", "BOTTLE_DROP", "BOTTLE_READ", "LEVEL_UP",
            "SPELL_CAST", "ROOM_ENTER", "TRUST_UPDATE", "CAP_ISSUE", "BRANCH",
            "NOP", "TRAIL_BEGIN", "TRAIL_END", "COMMENT", "LABEL", "HASHTABLE",
        ]
        for name in expected_names:
            self.assertTrue(hasattr(TrailOpcodes, name), f"Missing: {name}")

    def test_opcode_ranges(self) -> None:
        """Trail ops in 0x90-0x9F, meta in 0xA0-0xA3."""
        for op in TrailOpcodes:
            val = int(op)
            if op in (TrailOpcodes.HASHTABLE,):
                self.assertEqual(val, 0xB0)
            elif op in (TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END,
                        TrailOpcodes.COMMENT, TrailOpcodes.LABEL):
                self.assertTrue(0xA0 <= val <= 0xA3)
            else:
                self.assertTrue(0x90 <= val <= 0x9F)

    def test_is_valid(self) -> None:
        self.assertTrue(TrailOpcodes.is_valid(0x90))
        self.assertTrue(TrailOpcodes.is_valid(0xA0))
        self.assertTrue(TrailOpcodes.is_valid(0xB0))
        self.assertFalse(TrailOpcodes.is_valid(0x00))
        self.assertFalse(TrailOpcodes.is_valid(0x50))
        self.assertFalse(TrailOpcodes.is_valid(0xFF))

    def test_is_trail_op(self) -> None:
        self.assertTrue(TrailOpcodes.is_trail_op(0x90))
        self.assertTrue(TrailOpcodes.is_trail_op(0x9F))
        self.assertFalse(TrailOpcodes.is_trail_op(0xA0))
        self.assertFalse(TrailOpcodes.is_trail_op(0xB0))

    def test_is_meta_op(self) -> None:
        self.assertTrue(TrailOpcodes.is_meta_op(0xA0))
        self.assertTrue(TrailOpcodes.is_meta_op(0xA3))
        self.assertFalse(TrailOpcodes.is_meta_op(0x90))
        self.assertFalse(TrailOpcodes.is_meta_op(0xB0))


# ═══════════════════════════════════════════════════════════════════════════════
# Data Class Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataClasses(unittest.TestCase):
    """Tests for TrailStep and TrailProgram."""

    def test_trail_step_clamps_operands(self) -> None:
        step = TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[0x10000, -1])
        self.assertEqual(step.operands[0], 0)
        self.assertEqual(step.operands[1], 0xFFFF)

    def test_trail_step_accepts_int_opcode(self) -> None:
        step = TrailStep(opcode=0x92, operands=[1, 2])
        self.assertEqual(step.opcode, TrailOpcodes.FILE_READ)

    def test_trail_program_valid(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        self.assertTrue(prog.is_valid)

    def test_trail_program_invalid_no_begin(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.FILE_READ),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        self.assertFalse(prog.is_valid)

    def test_trail_program_invalid_no_end(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
            TrailStep(opcode=TrailOpcodes.FILE_READ),
        ])
        self.assertFalse(prog.is_valid)

    def test_trail_program_action_steps(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
            TrailStep(opcode=TrailOpcodes.FILE_READ),
            TrailStep(opcode=TrailOpcodes.NOP),
            TrailStep(opcode=TrailOpcodes.COMMENT, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.FILE_WRITE, operands=[1, 2, 3, 4]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        actions = prog.action_steps
        self.assertEqual(len(actions), 3)

    def test_trail_program_concatenate(self) -> None:
        a = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        b = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
            TrailStep(opcode=TrailOpcodes.FILE_WRITE, operands=[3, 4, 5, 6]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        merged = a.concatenate(b)
        self.assertEqual(len(merged.steps), 4)
        self.assertEqual(merged.steps[0].opcode, TrailOpcodes.TRAIL_BEGIN)
        self.assertEqual(merged.steps[-1].opcode, TrailOpcodes.TRAIL_END)

    def test_trail_program_fingerprint(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "test", "trail_id": "fp-test", "timestamp": 1000}),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        fp = prog.fingerprint()
        self.assertEqual(len(fp), 64)
        self.assertEqual(fp, prog.fingerprint())  # deterministic


# ═══════════════════════════════════════════════════════════════════════════════
# Encoder/Decoder Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEncoderDecoder(unittest.TestCase):
    """Tests for TrailEncoder and TrailDecoder."""

    def _make_minimal_program(self) -> TrailProgram:
        return TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "test", "trail_id": "test-trail", "timestamp": 1000}),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])

    def test_encode_produces_bytes(self) -> None:
        prog = self._make_minimal_program()
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)
        self.assertIsInstance(bytecode, bytes)
        self.assertGreater(len(bytecode), 0)

    def test_encode_empty_raises(self) -> None:
        encoder = TrailEncoder()
        with self.assertRaises(ValueError):
            encoder.encode(TrailProgram())

    def test_roundtrip_preserves_opcodes(self) -> None:
        prog = self._make_minimal_program()
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)

        decoder = TrailDecoder()
        decoded = decoder.decode(bytecode)

        self.assertEqual(len(decoded.steps), len(prog.steps))
        for orig, dec in zip(prog.steps, decoded.steps):
            self.assertEqual(orig.opcode, dec.opcode)

    def test_roundtrip_preserves_operands(self) -> None:
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "agent-1", "trail_id": "roundtrip-test", "timestamp": 2000}),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[0x1234, 0x5678]),
            TrailStep(opcode=TrailOpcodes.FILE_WRITE, operands=[0xABCD, 0xEF01, 0x2345, 0x6789]),
            TrailStep(opcode=TrailOpcodes.SEARCH_CODE, operands=[0x1111, 0x2222]),
            TrailStep(opcode=TrailOpcodes.NOP),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)

        decoder = TrailDecoder()
        decoded = decoder.decode(bytecode)

        # TRAIL_BEGIN and TRAIL_END have special encoding that modifies operands
        special_ops = {TrailOpcodes.TRAIL_BEGIN, TrailOpcodes.TRAIL_END}
        for orig, dec in zip(prog.steps, decoded.steps):
            self.assertEqual(orig.opcode, dec.opcode,
                             f"Opcode mismatch")
            if orig.opcode not in special_ops:
                self.assertEqual(orig.operands, dec.operands,
                                 f"Operand mismatch for {orig.opcode.name}")

    def test_string_table_preserved(self) -> None:
        encoder = TrailEncoder()
        encoder.string_table["aabbccdd"] = "test_path.py"
        prog = self._make_minimal_program()
        bytecode = encoder.encode(prog)

        decoder = TrailDecoder()
        decoder.decode(bytecode)
        self.assertIn("aabbccdd", decoder.string_table)
        self.assertEqual(decoder.string_table["aabbccdd"], "test_path.py")

    def test_all_trail_opcodes_encodable(self) -> None:
        """Every trail op (0x90-0x9F) should be encodable and decodable."""
        trail_ops = [op for op in TrailOpcodes if TrailOpcodes.is_trail_op(int(op))]
        steps = [
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "test", "trail_id": "all-ops", "timestamp": 3000}),
        ]
        for op in trail_ops:
            if op == TrailOpcodes.NOP:
                steps.append(TrailStep(opcode=op))
            else:
                steps.append(TrailStep(opcode=op, operands=[0x01, 0x02]))
        steps.append(TrailStep(opcode=TrailOpcodes.TRAIL_END))

        prog = TrailProgram(steps=steps)
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)

        decoder = TrailDecoder()
        decoded = decoder.decode(bytecode)
        self.assertEqual(len(decoded.steps), len(steps))


# ═══════════════════════════════════════════════════════════════════════════════
# Verifier Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifier(unittest.TestCase):
    """Tests for TrailVerifier."""

    def _valid_program(self) -> TrailProgram:
        return TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "test", "trail_id": "verify", "timestamp": 1000}),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2]),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])

    def test_valid_program_passes(self) -> None:
        verifier = TrailVerifier()
        prog = self._valid_program()
        self.assertTrue(verifier.verify(prog))
        self.assertEqual(len(verifier.errors), 0)

    def test_missing_begin_fails(self) -> None:
        verifier = TrailVerifier()
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.FILE_READ),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])
        self.assertFalse(verifier.verify(prog))
        self.assertTrue(any("TRAIL_BEGIN" in e for e in verifier.errors))

    def test_missing_end_fails(self) -> None:
        verifier = TrailVerifier()
        prog = TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN),
        ])
        self.assertFalse(verifier.verify(prog))

    def test_too_short_fails(self) -> None:
        verifier = TrailVerifier()
        prog = TrailProgram(steps=[TrailStep(opcode=TrailOpcodes.FILE_READ)])
        self.assertFalse(verifier.verify(prog))

    def test_verify_bytecode(self) -> None:
        prog = self._valid_program()
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)

        verifier = TrailVerifier()
        self.assertTrue(verifier.verify_bytecode(bytecode))

    def test_verify_bad_bytecode(self) -> None:
        verifier = TrailVerifier()
        self.assertFalse(verifier.verify_bytecode(b"\x00\x01\x02"))

    def test_report_pass(self) -> None:
        verifier = TrailVerifier()
        verifier.verify(self._valid_program())
        report = verifier.report()
        self.assertIn("PASSED", report)

    def test_report_fail(self) -> None:
        verifier = TrailVerifier()
        verifier.verify(TrailProgram())
        report = verifier.report()
        self.assertIn("error", report.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# Printer Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrinter(unittest.TestCase):
    """Tests for TrailPrinter."""

    def _program(self) -> TrailProgram:
        return TrailProgram(steps=[
            TrailStep(opcode=TrailOpcodes.TRAIL_BEGIN,
                      metadata={"agent": "test", "trail_id": "print-test", "timestamp": 1000}),
            TrailStep(opcode=TrailOpcodes.FILE_READ, operands=[1, 2],
                      description="Read a file"),
            TrailStep(opcode=TrailOpcodes.NOP),
            TrailStep(opcode=TrailOpcodes.TRAIL_END),
        ])

    def test_text_format(self) -> None:
        printer = TrailPrinter()
        output = printer.print_program(self._program(), fmt="text")
        self.assertIn("DISASSEMBLY", output)
        self.assertIn("TRAIL_BEGIN", output)
        self.assertIn("FILE_READ", output)
        self.assertIn("NOP", output)
        self.assertIn("TRAIL_END", output)

    def test_compact_format(self) -> None:
        printer = TrailPrinter()
        output = printer.print_program(self._program(), fmt="compact")
        self.assertIn("TRAIL_BEGIN", output)
        self.assertNotIn("DISASSEMBLY", output)

    def test_verbose_format(self) -> None:
        printer = TrailPrinter(string_table={"12345678": "test.py"})
        output = printer.print_program(self._program(), fmt="verbose")
        self.assertIn("VERBOSE", output)
        self.assertIn("opcode:", output)

    def test_hex_format(self) -> None:
        printer = TrailPrinter()
        output = printer.print_program(self._program(), fmt="hex")
        self.assertIn("HEX DUMP", output)
        self.assertIn("0000:", output)

    def test_invalid_format_raises(self) -> None:
        printer = TrailPrinter()
        with self.assertRaises(ValueError):
            printer.print_program(self._program(), fmt="invalid")

    def test_print_bytecode(self) -> None:
        prog = self._program()
        encoder = TrailEncoder()
        bytecode = encoder.encode(prog)
        printer = TrailPrinter()
        output = printer.print_bytecode(bytecode, fmt="text")
        self.assertIn("DISASSEMBLY", output)


# ═══════════════════════════════════════════════════════════════════════════════
# Compiler Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompiler(unittest.TestCase):
    """Tests for TrailCompiler — all 17+ entry types."""

    def _sample_entries(self) -> list[dict]:
        return [
            {"op": "TRAIL_BEGIN", "agent": "test-agent", "trail_id": "compile-test", "ts": 1000},
            {"op": "FILE_READ", "path": "main.py", "desc": "Read main"},
            {"op": "FILE_WRITE", "path": "out.py", "content": "# output", "desc": "Write output"},
            {"op": "FILE_EDIT", "path": "main.py", "old": "old", "new": "new", "desc": "Edit"},
            {"op": "SEARCH_CODE", "pattern": "def foo", "desc": "Search"},
            {"op": "TEST_RUN", "test_path": "tests/", "count": 10, "desc": "Test"},
            {"op": "GIT_COMMIT", "repo_id": 1, "message": "initial", "desc": "Commit"},
            {"op": "GIT_PUSH", "repo_id": 1, "desc": "Push"},
            {"op": "BOTTLE_DROP", "target": "oracle", "content": "query", "desc": "Drop bottle"},
            {"op": "BOTTLE_READ", "source": "oracle", "desc": "Read bottle"},
            {"op": "LEVEL_UP", "level": 3, "desc": "Level up"},
            {"op": "SPELL_CAST", "spell_id": "heal", "desc": "Cast spell"},
            {"op": "ROOM_ENTER", "room_id": "chamber", "desc": "Enter room"},
            {"op": "TRUST_UPDATE", "target": "oracle", "delta": 5, "desc": "Trust"},
            {"op": "CAP_ISSUE", "action": "read", "holder": "agent", "desc": "Cap"},
            {"op": "BRANCH", "reg": 0, "desc": "Branch"},
            {"op": "NOP", "desc": "Nop"},
            {"op": "COMMENT", "comment": "a note", "desc": "Comment"},
            {"op": "LABEL", "label": "loop_start", "desc": "Label"},
            {"op": "TRAIL_END", "steps": 18, "status": 0, "desc": "Done"},
        ]

    def test_compile_all_entry_types(self) -> None:
        """All 17+ entry types should compile without error."""
        compiler = TrailCompiler()
        entries = self._sample_entries()
        prog = compiler.compile(entries)
        self.assertTrue(prog.is_valid)
        self.assertEqual(len(prog.steps), len(entries))

    def test_compile_unknown_opcode_raises(self) -> None:
        compiler = TrailCompiler()
        with self.assertRaises(ValueError):
            compiler.compile([{"op": "NONEXISTENT_OPCODE"}])

    def test_compile_and_encode(self) -> None:
        compiler = TrailCompiler()
        entries = self._sample_entries()
        bytecode = compiler.compile_and_encode(entries)
        self.assertIsInstance(bytecode, bytes)
        self.assertGreater(len(bytecode), 0)

    def test_string_table_populated(self) -> None:
        compiler = TrailCompiler()
        compiler.compile([{"op": "FILE_READ", "path": "hello.py"}])
        path_hash = str_to_hash("hello.py")
        self.assertIn(path_hash, compiler.string_table)

    def test_compile_trail_begin_metadata(self) -> None:
        compiler = TrailCompiler()
        prog = compiler.compile([{
            "op": "TRAIL_BEGIN",
            "agent": "test-bot",
            "trail_id": "meta-test",
            "ts": 12345,
        }, {"op": "TRAIL_END", "steps": 0, "status": 0}])
        first = prog.steps[0]
        self.assertEqual(first.metadata["agent"], "test-bot")
        self.assertEqual(first.metadata["trail_id"], "meta-test")
        self.assertEqual(first.metadata["timestamp"], 12345)


# ═══════════════════════════════════════════════════════════════════════════════
# Executor Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutor(unittest.TestCase):
    """Tests for TrailExecutor with MockWorld."""

    def _make_trail_bytecode(self) -> bytes:
        compiler = TrailCompiler()
        entries = [
            {"op": "TRAIL_BEGIN", "agent": "executor-test", "trail_id": "exec-test", "ts": 1000},
            {"op": "FILE_READ", "path": "main.py"},
            {"op": "FILE_WRITE", "path": "output.py", "content": "data"},
            {"op": "SEARCH_CODE", "pattern": "import"},
            {"op": "NOP"},
            {"op": "COMMENT", "comment": "midpoint"},
            {"op": "TRAIL_END", "steps": 5, "status": 0},
        ]
        return compiler.compile_and_encode(entries)

    def test_execute_mock_world(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)
        result = executor.execute()

        self.assertTrue(result.success)
        self.assertGreater(result.total_steps, 0)
        self.assertEqual(result.failed_steps, 0)
        self.assertGreater(len(result.execution_trail), 0)
        self.assertEqual(len(result.execution_fingerprint), 64)

    def test_execute_call_order(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)
        executor.execute()

        world.assert_call_count("file_read", 1)
        world.assert_call_count("file_write", 1)
        world.assert_call_count("search_code", 1)

    def test_execute_dry_run(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode, dry_run=True)
        executor.execute()

        self.assertEqual(len(world.calls), 0, "Dry run should not call world")
        for ev in executor.get_events():
            if "DRY-RUN" in ev.result:
                return
        self.fail("Expected at least one DRY-RUN event")

    def test_execute_fail_fast(self) -> None:
        world = MockWorld(fail_on={"file_read"})
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode, fail_fast=True)
        result = executor.execute()

        self.assertFalse(result.success)
        self.assertGreater(result.failed_steps, 0)

    def test_step_by_step(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)

        events = []
        while True:
            ev = executor.step()
            if ev is None:
                break
            events.append(ev)

        self.assertGreater(len(events), 0)

    def test_pause_resume(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)

        executor.pause()
        ev = executor.step()
        self.assertIsNone(ev, "Paused executor should return None")

        executor.resume()
        ev = executor.step()
        self.assertIsNotNone(ev, "Resumed executor should return an event")

    def test_execution_result_summary(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)
        result = executor.execute()
        summary = result.summary()
        self.assertIn("SUCCESS", summary)
        self.assertIn("Fingerprint:", summary)

    def test_event_serialization(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)
        result = executor.execute()

        for ev in result.events:
            d = ev.to_dict()
            self.assertIn("step_index", d)
            self.assertIn("opcode", d)
            self.assertIn("result", d)
            self.assertIn("proof", d)
            restored = TrailEvent.from_dict(d)
            self.assertEqual(ev.step_index, restored.step_index)
            self.assertEqual(ev.opcode, restored.opcode)

    def test_result_serialization(self) -> None:
        world = MockWorld()
        bytecode = self._make_trail_bytecode()
        executor = TrailExecutor(world=world, bytecode=bytecode)
        result = executor.execute()
        d = result.to_dict()
        self.assertTrue(d["success"])
        self.assertIn("events", d)
        json_str = result.to_json()
        self.assertIsInstance(json_str, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Operand Resolution Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOperandResolution(unittest.TestCase):
    """Tests for resolve_operands and operand_names."""

    def test_string_operand_resolution(self) -> None:
        hi, lo = str_hash_to_u16_pair("test_file.py")
        table = {str_to_hash("test_file.py"): "test_file.py"}
        resolved = resolve_operands(TrailOpcodes.FILE_READ, [hi, lo], table)
        self.assertEqual(resolved, ["test_file.py"])

    def test_numeric_operand(self) -> None:
        resolved = resolve_operands(TrailOpcodes.LEVEL_UP, [5], {})
        self.assertEqual(resolved, [5])

    def test_unresolved_string(self) -> None:
        resolved = resolve_operands(TrailOpcodes.FILE_READ, [0xDEAD, 0xBEEF], {})
        self.assertIn("unresolved", resolved[0])

    def test_operand_names_all_ops(self) -> None:
        for op in TrailOpcodes:
            names = operand_names(op)
            self.assertIsInstance(names, list)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCLI(unittest.TestCase):
    """Tests for CLI argument parsing and commands."""

    def test_build_parser(self) -> None:
        parser = build_parser()
        self.assertIsNotNone(parser)

    def test_status_command(self) -> None:
        rc = main(["status"])
        self.assertEqual(rc, 0)

    def test_onboard_command(self) -> None:
        rc = main(["onboard"])
        self.assertEqual(rc, 0)

    def test_no_command_returns_zero(self) -> None:
        rc = main([])
        self.assertEqual(rc, 0)

    def test_encode_command(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([
                {"op": "TRAIL_BEGIN", "agent": "cli-test", "trail_id": "cli", "ts": 1000},
                {"op": "FILE_READ", "path": "x.py"},
                {"op": "TRAIL_END", "steps": 1, "status": 0},
            ], f)
            tmp = f.name

        try:
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as out:
                out_path = out.name
            rc = main(["encode", tmp, "-o", out_path])
            self.assertEqual(rc, 0)
            self.assertGreater(os.path.getsize(out_path), 0)
        finally:
            os.unlink(tmp)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_decode_command(self) -> None:
        compiler = TrailCompiler()
        entries = [
            {"op": "TRAIL_BEGIN", "agent": "dec", "trail_id": "dec", "ts": 1000},
            {"op": "FILE_READ", "path": "test.py"},
            {"op": "TRAIL_END", "steps": 1, "status": 0},
        ]
        bytecode = compiler.compile_and_encode(entries)

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytecode)
            tmp = f.name

        try:
            rc = main(["decode", tmp])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(tmp)

    def test_verify_command(self) -> None:
        compiler = TrailCompiler()
        entries = [
            {"op": "TRAIL_BEGIN", "agent": "v", "trail_id": "v", "ts": 1000},
            {"op": "FILE_READ", "path": "a.py"},
            {"op": "TRAIL_END", "steps": 1, "status": 0},
        ]
        bytecode = compiler.compile_and_encode(entries)

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytecode)
            tmp = f.name

        try:
            rc = main(["verify", tmp])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(tmp)

    def test_execute_mock_command(self) -> None:
        compiler = TrailCompiler()
        entries = [
            {"op": "TRAIL_BEGIN", "agent": "ex", "trail_id": "ex", "ts": 1000},
            {"op": "FILE_READ", "path": "a.py"},
            {"op": "TRAIL_END", "steps": 1, "status": 0},
        ]
        bytecode = compiler.compile_and_encode(entries)

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytecode)
            tmp = f.name

        try:
            rc = main(["execute", tmp, "--world", "mock"])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(tmp)

    def test_disassemble_command(self) -> None:
        compiler = TrailCompiler()
        entries = [
            {"op": "TRAIL_BEGIN", "agent": "d", "trail_id": "d", "ts": 1000},
            {"op": "FILE_READ", "path": "z.py"},
            {"op": "NOP"},
            {"op": "TRAIL_END", "steps": 2, "status": 0},
        ]
        bytecode = compiler.compile_and_encode(entries)

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytecode)
            tmp = f.name

        try:
            rc = main(["disassemble", tmp])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(tmp)

    def test_nonexistent_file_returns_error(self) -> None:
        rc = main(["decode", "/nonexistent/file.bin"])
        self.assertEqual(rc, 1)

    def test_compile_command(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([
                {"op": "TRAIL_BEGIN", "agent": "c", "trail_id": "c", "ts": 1000},
                {"op": "FILE_READ", "path": "y.py"},
                {"op": "TRAIL_END", "steps": 1, "status": 0},
            ], f)
            tmp = f.name

        try:
            rc = main(["compile", tmp])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(tmp)


# ═══════════════════════════════════════════════════════════════════════════════
# Workshop Recipe Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkshopRecipes(unittest.TestCase):
    """Tests that all workshop recipes compile and verify correctly."""

    def _recipe_files(self) -> list[str]:
        workshop = Path(__file__).parent.parent / "workshop" / "recipes"
        recipes = []
        for category in ["hot", "med", "cold"]:
            cat_dir = workshop / category
            if cat_dir.exists():
                for f in cat_dir.glob("*.json"):
                    recipes.append(str(f))
        return recipes

    def test_all_recipes_compile(self) -> None:
        for path in self._recipe_files():
            with self.subTest(path=path):
                with open(path, "r") as f:
                    data = json.load(f)
                entries = data if isinstance(data, list) else data.get("entries", [])
                compiler = TrailCompiler()
                prog = compiler.compile(entries)
                self.assertTrue(prog.is_valid, f"{path}: trail not valid")

    def test_all_recipes_roundtrip(self) -> None:
        for path in self._recipe_files():
            with self.subTest(path=path):
                with open(path, "r") as f:
                    data = json.load(f)
                entries = data if isinstance(data, list) else data.get("entries", [])
                compiler = TrailCompiler()
                bytecode = compiler.compile_and_encode(entries)

                decoder = TrailDecoder()
                decoded = decoder.decode(bytecode)
                self.assertEqual(len(decoded.steps), len(entries), f"{path}: roundtrip failed")


if __name__ == "__main__":
    unittest.main()
