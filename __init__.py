"""
Trail Agent — standalone trail encoder/decoder/executor.

Extracted from holodeck-studio into a self-contained CLI agent.
Zero external dependencies — uses only Python stdlib.

Public API:
    trail_codec    — TrailOpcodes, TrailStep, TrailProgram, TrailEncoder,
                     TrailDecoder, TrailPrinter, TrailVerifier, hash utils
    trail_compiler — TrailCompiler (worklog entries -> TrailProgram)
    trail_executor — TrailExecutor, MockWorld, FileWorld, TrailEvent, TrailResult
"""

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
    HashTableEntry,
)
from trail_compiler import TrailCompiler
from trail_executor import (
    TrailExecutor,
    TrailEvent,
    TrailResult,
    MockWorld,
    FileWorld,
    WorldInterface,
    resolve_operands,
    operand_names,
)

__all__ = [
    "TrailOpcodes", "TrailStep", "TrailProgram", "TrailEncoder", "TrailDecoder",
    "TrailPrinter", "TrailVerifier", "str_to_hash", "str_hash_to_u16_pair",
    "u16_pair_to_hex", "HashTableEntry",
    "TrailCompiler",
    "TrailExecutor", "TrailEvent", "TrailResult", "MockWorld", "FileWorld",
    "WorldInterface", "resolve_operands", "operand_names",
]
