# trail-agent

**Python agent for executing trails — part of the Cocapn Fleet**  
<https://github.com/SuperInstance>

## Overview

`trail-agent` provides a lightweight, extensible framework to compile, encode, and execute trail definitions. It implements the **Trail-FLUX bytecode format** — a compact binary representation of an agent's journey through a codebase. The core philosophy is: *"The trail IS the code."* An agent's worklog is a program that, when executed in order, reproduces the agent's every action.

The agent is designed around a clean pipeline: **worklog entries → compiler → bytecode → executor → execution proof**. Every step is cryptographically verifiable via SHA-256 fingerprints, making trails tamper-evident and fully auditable. The entire system has zero external dependencies — it uses only the Python standard library.

### Key Features

- **21 opcodes** covering file I/O, git operations, inter-agent messaging, trust updates, and control flow
- **Compact bytecode** with string hash tables — typically 5–20 bytes per step
- **Sandboxed execution** through a `WorldInterface` protocol (MockWorld for tests, FileWorld for real ops)
- **6-pass verification** — structural, opcode, operand count, round-trip, fingerprint, and hash table integrity
- **4 output formats** for disassembly — text, hex, verbose, and compact
- **CLI with 8 subcommands** — encode, decode, verify, execute, compile, disassemble, onboard, status
- **Zero dependencies** — pure Python stdlib, no pip install required

## Architecture

The trail-agent follows a three-stage pipeline architecture:

```
┌──────────────────┐    ┌───────────────────┐    ┌───────────────────┐
│  TrailCompiler    │───▶│  TrailEncoder    │───▶│  TrailExecutor    │
│  (entries→steps)  │    │  (steps→bytecode) │    │  (bytecode→ops)   │
└──────────────────┘    └───────────────────┘    └───────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
   TrailProgram            FLUX Bytecode           TrailResult
   (ordered steps)         (compact binary)       (events + proof)
```

### How It Works

1. **Compile**: The `TrailCompiler` converts structured worklog entries (Python dicts with an `op` key) into a `TrailProgram` — an ordered list of `TrailStep` objects. Each step contains an opcode, operands (encoded as compact u16 hash references), and optional metadata.

2. **Encode**: The `TrailEncoder` serializes a `TrailProgram` into compact FLUX bytecode. String operands are hashed to 4-byte SHA-256 prefixes and stored in a trailing hash table, making the bytecode self-contained and compact. The format supports TRAIL_BEGIN/END framing, per-step opcodes with variable-length operands, and an append-only string hash table for round-trip fidelity.

3. **Execute**: The `TrailExecutor` replays compiled bytecode against a `WorldInterface`. Every operation is dispatched through the interface — file reads/writes go to the filesystem, git operations invoke subprocesses, and inter-agent messages go to the bottle system. Each step emits a `TrailEvent` with a SHA-256 proof hash, and the executor produces a `TrailResult` containing the full execution trail and a cryptographic fingerprint.

### Opcode Map

Trail-FLUX opcodes are organized into three ranges:

| Range | Category | Count | Purpose |
|-------|----------|-------|---------|
| `0x90–0x9F` | Trail Operations | 16 | File I/O, git, messaging, trust, control flow |
| `0xA0–0xA3` | Meta Operations | 4 | Trail framing, comments, labels |
| `0xB0+` | Markers | 1 | String hash table section |

**Trail Operations**: `GIT_COMMIT`, `GIT_PUSH`, `FILE_READ`, `FILE_WRITE`, `FILE_EDIT`, `TEST_RUN`, `SEARCH_CODE`, `BOTTLE_DROP`, `BOTTLE_READ`, `LEVEL_UP`, `SPELL_CAST`, `ROOM_ENTER`, `TRUST_UPDATE`, `CAP_ISSUE`, `BRANCH`, `NOP`

**Meta Operations**: `TRAIL_BEGIN`, `TRAIL_END`, `COMMENT`, `LABEL`

### Bytecode Format

```
[0xA0] [agent_id: u8] [trail_id: 4 bytes] [timestamp: 4 bytes]  → TRAIL_BEGIN
[opcode: u8] [operand_count: u8] [operands: variable u16...]        → each step
[0xA1] [total_steps: u16] [status: u8]                          → TRAIL_END
[0xB0] [table_length: u16]
  [hash: 8 bytes] [string_length: u8] [string_bytes: variable]   → STRING TABLE
```

## Quick Start

### Installation

```bash
git clone https://github.com/SuperInstance/trail-agent.git
cd trail-agent
pip install -e .
```

### Basic Usage

```bash
# Run the CLI
python -m trail_agent --help

# Compile a worklog to bytecode
python -m trail_agent encode worklog.json -o trail.bin

# Decode bytecode to human-readable text
python -m trail_agent decode trail.bin --format verbose

# Verify trail integrity (6 automated checks)
python -m trail_agent verify trail.bin

# Execute a trail with mock world (no side effects)
python -m trail_agent execute trail.bin --world mock

# Execute with real filesystem
python -m trail_agent execute trail.bin --world file --base-dir ./my-project

# Dry-run (log steps without executing)
python -m trail_agent execute trail.bin --dry-run

# Show raw opcodes and operands
python -m trail_agent disassemble trail.bin
```

### Programmatic API

```python
from trail_codec import TrailOpcodes, TrailEncoder, TrailDecoder, TrailPrinter, TrailVerifier
from trail_compiler import TrailCompiler
from trail_executor import TrailExecutor, MockWorld, FileWorld

# Compile worklog entries to bytecode
compiler = TrailCompiler()
entries = [
    {"op": "TRAIL_BEGIN", "agent": "my-agent", "trail_id": "hello-world"},
    {"op": "FILE_READ", "path": "/src/main.py", "desc": "Read main entry point"},
    {"op": "TEST_RUN", "test_path": "tests/", "count": 42, "desc": "Run all tests"},
    {"op": "TRAIL_END", "steps": 3, "status": 0, "desc": "Trail ends"},
]
program = compiler.compile(entries)
encoder = TrailEncoder(string_table=dict(compiler.string_table))
bytecode = encoder.encode(program)
print(f"Fingerprint: {program.fingerprint()[:32]}...")

# Decode and pretty-print
printer = TrailPrinter(string_table=dict(compiler.string_table))
print(printer.print_program(program, fmt="verbose"))

# Execute with mock world
executor = TrailExecutor(world=MockWorld(), bytecode=bytecode)
result = executor.execute()
print(result.summary())

# Execute with real filesystem
executor = TrailExecutor(world=FileWorld(base_dir="."), bytecode=bytecode)
result = executor.execute(dry_run=True)
print(result.summary())
```

### Verify Trail Integrity

```python
from trail_codec import TrailVerifier

verifier = TrailVerifier()
passed = verifier.verify_bytecode(bytecode)
print(verifier.report())
# [PASS] Trail verification PASSED -- all checks clean
```

## Integration

### With the Cocapn Fleet

The trail-agent integrates with other fleet agents through the bottle messaging system (`BOTTLE_DROP` / `BOTTLE_READ` opcodes). Agents can share executable trail programs by dropping them into fleet message channels, enabling replay and verification of work across the fleet.

### Workshop Structure

The `workshop/` directory contains trail recipes and exercises:

- `recipes/hot/` — Immediate-use trail recipes (git workflows, file operations)
- `recipes/med/` — Multi-step composable trail recipes (trust communication)
- `recipes/cold/` — Advanced composable trail patterns
- `bootcamp/` — Learning exercises (hello trail, round trip)
- `dojo/` — Mastery challenges (string concatenation)

## Testing

```bash
# Run the test suite
pytest tests/test_trail_agent.py

# Run with verbose output
pytest tests/ -v
```

## Related

- **Cocapn Fleet** – the larger ecosystem: <https://github.com/SuperInstance>
- **Documentation & Wiki** – <https://github.com/SuperInstance/trail-agent/wiki>
- **Other agents** – see the `workshop/` directory for examples and extensions.

## License

See the [LICENSE](LICENSE) file for details.

---

<img src="callsign1.jpg" width="128" alt="callsign">
