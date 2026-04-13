"""
Trail Agent CLI — command-line interface for the standalone trail encoder/decoder/executor.

Subcommands:
    encode <worklog.json>           Compile a worklog JSON to trail bytecode (.bin)
    decode <trail.bin>              Decode trail bytecode to human-readable text
    verify <trail.bin>              Verify trail integrity (6 checks)
    execute <trail.bin> [--world]   Execute a trail with mock or file world
    compile <entries.json>          Compile entries to trail program (text output)
    disassemble <trail.bin>         Show raw opcodes and operands
    onboard                         Set up the agent workspace
    status                          Show agent status and capabilities

Usage:
    python -m trail_agent encode worklog.json -o trail.bin
    python -m trail_agent decode trail.bin --format verbose
    python -m trail_agent verify trail.bin
    python -m trail_agent execute trail.bin --world mock
    python -m trail_agent compile entries.json
    python -m trail_agent disassemble trail.bin
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure local imports work when running as module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trail_codec import (
    TrailOpcodes,
    TrailEncoder,
    TrailDecoder,
    TrailPrinter,
    TrailProgram,
    TrailStep,
    TrailVerifier,
)
from trail_compiler import TrailCompiler
from trail_executor import (
    TrailExecutor,
    MockWorld,
    FileWorld,
    WorldInterface,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_encode(args: argparse.Namespace) -> int:
    """Encode/compile a worklog JSON file to trail bytecode."""
    input_path = args.worklog
    output_path = args.output

    if not os.path.exists(input_path):
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = data if isinstance(data, list) else data.get("entries", [])
    if not entries:
        print("ERROR: no entries found in worklog", file=sys.stderr)
        return 1

    compiler = TrailCompiler()
    program = compiler.compile(entries)
    encoder = TrailEncoder(string_table=dict(compiler.string_table))
    bytecode = encoder.encode(program)

    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".bin"

    with open(output_path, "wb") as f:
        f.write(bytecode)

    fp = program.fingerprint()
    action_count = len(program.action_steps)
    print(f"Encoded {action_count} action steps ({len(program.steps)} total)")
    print(f"Output: {output_path} ({len(bytecode)} bytes)")
    print(f"Fingerprint: {fp[:32]}...")
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    """Decode trail bytecode to human-readable text."""
    trail_path = args.trail

    if not os.path.exists(trail_path):
        print(f"ERROR: file not found: {trail_path}", file=sys.stderr)
        return 1

    with open(trail_path, "rb") as f:
        bytecode = f.read()

    fmt = args.format or "text"
    printer = TrailPrinter()
    try:
        output = printer.print_bytecode(bytecode, fmt=fmt)
        print(output)
    except Exception as e:
        print(f"ERROR: failed to decode: {e}", file=sys.stderr)
        return 1

    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify trail bytecode integrity."""
    trail_path = args.trail

    if not os.path.exists(trail_path):
        print(f"ERROR: file not found: {trail_path}", file=sys.stderr)
        return 1

    with open(trail_path, "rb") as f:
        bytecode = f.read()

    verifier = TrailVerifier()
    passed = verifier.verify_bytecode(bytecode)
    print(verifier.report())

    if not passed:
        return 1

    if args.show_fingerprint:
        import hashlib
        fp = hashlib.sha256(bytecode).hexdigest()
        print(f"Fingerprint: {fp}")

    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    """Execute a trail bytecode file."""
    trail_path = args.trail

    if not os.path.exists(trail_path):
        print(f"ERROR: file not found: {trail_path}", file=sys.stderr)
        return 1

    with open(trail_path, "rb") as f:
        bytecode = f.read()

    world_type = args.world or "mock"

    if world_type == "mock":
        world: WorldInterface = MockWorld()
    elif world_type == "file":
        base_dir = args.base_dir or "."
        world = FileWorld(base_dir=base_dir, backup_on_write=not args.no_backup)
    else:
        print(f"ERROR: unknown world type: {world_type} (use 'mock' or 'file')",
              file=sys.stderr)
        return 1

    executor = TrailExecutor(
        world=world,
        bytecode=bytecode,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
    )

    result = executor.execute()
    print(result.summary())

    if not result.success:
        return 1
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    """Compile entries JSON to a trail program (text output)."""
    input_path = args.entries

    if not os.path.exists(input_path):
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = data if isinstance(data, list) else data.get("entries", [])
    if not entries:
        print("ERROR: no entries found", file=sys.stderr)
        return 1

    compiler = TrailCompiler()
    program = compiler.compile(entries)

    printer = TrailPrinter(string_table=dict(compiler.string_table))
    output = printer.print_program(program, fmt=args.format or "text")
    print(output)

    return 0


def cmd_disassemble(args: argparse.Namespace) -> int:
    """Show raw opcodes and operands of trail bytecode."""
    trail_path = args.trail

    if not os.path.exists(trail_path):
        print(f"ERROR: file not found: {trail_path}", file=sys.stderr)
        return 1

    with open(trail_path, "rb") as f:
        bytecode = f.read()

    decoder = TrailDecoder()
    program = decoder.decode(bytecode)

    print("=== RAW DISASSEMBLY ===")
    print(f"File size: {len(bytecode)} bytes")
    print(f"Steps: {len(program.steps)}")
    print(f"String table entries: {len(decoder.string_table)}")
    print("")

    for i, step in enumerate(program.steps):
        op = step.opcode
        op_hex = f"0x{int(op):02X}"
        ops_str = ", ".join(f"0x{o:04X}" for o in step.operands)
        meta_str = ""
        if step.metadata:
            meta_str = f"  meta={step.metadata}"
        print(f"  [{i:03d}] {op.name:14s} {op_hex}  operands=[{ops_str}]{meta_str}")

    print("")
    if decoder.string_table:
        print("--- STRING TABLE ---")
        for h, s in sorted(decoder.string_table.items()):
            print(f"  {h} -> \"{s}\"")

    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    """Set up the agent workspace."""
    agent_root = Path(__file__).parent
    print("=== Trail Agent Onboarding ===")
    print("")

    dirs_to_create = [
        agent_root / "workshop" / "recipes" / "hot",
        agent_root / "workshop" / "recipes" / "med",
        agent_root / "workshop" / "recipes" / "cold",
        agent_root / "workshop" / "bootcamp",
        agent_root / "workshop" / "dojo",
        agent_root / "tests",
        agent_root / "output",
    ]

    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  [+] {d.relative_to(agent_root)}")

    print("")
    print("Capabilities:")
    print(f"  Opcodes:    {len(TrailOpcodes)} defined")
    print(f"  Encoder:    TrailEncoder (TrailProgram -> bytecode)")
    print(f"  Decoder:    TrailDecoder (bytecode -> TrailProgram)")
    print(f"  Compiler:   TrailCompiler (worklog entries -> TrailProgram)")
    print(f"  Executor:   TrailExecutor (bytecode -> WorldInterface calls)")
    print(f"  Verifier:   TrailVerifier (6 integrity checks)")
    print(f"  Printer:    TrailPrinter (4 output formats: text/hex/verbose/compact)")
    print("")
    print("Workshop structure:")
    print("  recipes/hot/   — Immediate-use trail recipes")
    print("  recipes/med/   — Multi-step trail recipes")
    print("  recipes/cold/  — Advanced/composable trail recipes")
    print("  bootcamp/      — Learning exercises")
    print("  dojo/          — Mastery challenges")
    print("")
    print("Onboarding complete.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show agent status and capabilities."""
    print("=== Trail Agent Status ===")
    print("")

    print("Module versions:")
    print(f"  trail_codec.py     — Opcodes: {len(TrailOpcodes)}")
    print(f"  trail_compiler.py  — TrailCompiler")
    print(f"  trail_executor.py  — MockWorld + FileWorld")
    print("")

    print("Supported opcodes:")
    op_groups = {
        "Trail Operations (0x90-0x9F)": [
            op for op in TrailOpcodes if TrailOpcodes.is_trail_op(int(op))
        ],
        "Meta Operations (0xA0-0xA3)": [
            op for op in TrailOpcodes if TrailOpcodes.is_meta_op(int(op))
        ],
        "Markers (0xB0+)": [
            op for op in TrailOpcodes if int(op) >= 0xB0
        ],
    }

    for group_name, ops in op_groups.items():
        if ops:
            names = ", ".join(op.name for op in ops)
            print(f"  {group_name}: {names}")

    print("")
    print("CLI subcommands:")
    cmds = ["encode", "decode", "verify", "execute", "compile",
            "disassemble", "onboard", "status"]
    for cmd in cmds:
        print(f"  trail_agent {cmd}")

    print("")
    print("Dependencies: NONE (stdlib only)")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Argument Parser
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    """Build the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="trail_agent",
        description="Standalone trail encoder/decoder/executor CLI.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # encode
    p_enc = sub.add_parser("encode", help="Compile worklog JSON to trail bytecode")
    p_enc.add_argument("worklog", help="Path to worklog JSON file")
    p_enc.add_argument("-o", "--output", help="Output .bin file path")

    # decode
    p_dec = sub.add_parser("decode", help="Decode trail bytecode to text")
    p_dec.add_argument("trail", help="Path to trail .bin file")
    p_dec.add_argument("--format", choices=["text", "hex", "verbose", "compact"],
                       default="text", help="Output format (default: text)")

    # verify
    p_ver = sub.add_parser("verify", help="Verify trail integrity")
    p_ver.add_argument("trail", help="Path to trail .bin file")
    p_ver.add_argument("--show-fingerprint", action="store_true",
                       help="Show bytecode fingerprint")

    # execute
    p_exe = sub.add_parser("execute", help="Execute a trail bytecode file")
    p_exe.add_argument("trail", help="Path to trail .bin file")
    p_exe.add_argument("--world", choices=["mock", "file"], default="mock",
                       help="World implementation (default: mock)")
    p_exe.add_argument("--base-dir", default=".",
                       help="Base directory for FileWorld")
    p_exe.add_argument("--dry-run", action="store_true",
                       help="Log steps without executing")
    p_exe.add_argument("--fail-fast", action="store_true",
                       help="Stop on first error")
    p_exe.add_argument("--no-backup", action="store_true",
                       help="Disable file backups in FileWorld")

    # compile
    p_cmp = sub.add_parser("compile", help="Compile entries to trail program (text)")
    p_cmp.add_argument("entries", help="Path to entries JSON file")
    p_cmp.add_argument("--format", choices=["text", "hex", "verbose", "compact"],
                       default="text", help="Output format (default: text)")

    # disassemble
    p_dis = sub.add_parser("disassemble", help="Show raw opcodes and operands")
    p_dis.add_argument("trail", help="Path to trail .bin file")

    # onboard
    sub.add_parser("onboard", help="Set up the agent workspace")

    # status
    sub.add_parser("status", help="Show agent status and capabilities")

    return parser


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

COMMAND_MAP = {
    "encode": cmd_encode,
    "decode": cmd_decode,
    "verify": cmd_verify,
    "execute": cmd_execute,
    "compile": cmd_compile,
    "disassemble": cmd_disassemble,
    "onboard": cmd_onboard,
    "status": cmd_status,
}


def main(argv: Optional[list[str]] = None) -> int:
    """Main CLI entry point. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
