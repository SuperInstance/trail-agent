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

### Three-Stage Pipeline

The trail-agent follows a three-stage pipeline architecture:

```
┌──────────────────┐    ┌───────────────────┐    ┌───────────────────┐
│  TrailCompiler    │───>│  TrailEncoder     │───>│  TrailExecutor     │
│  (entries->steps) │    │  (steps->bytecode) │    │  (bytecode->ops)   │
└──────────────────┘    └───────────────────┘    └───────────────────┘
        |                        |                        |
        v                        v                        v
   TrailProgram            FLUX Bytecode           TrailResult
   (ordered steps)         (compact binary)       (events + proof)
```

### Full System Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │                  trail-agent                 │
                    │                                              │
  Worklog JSON ──>  │  ┌──────────────┐                           │
  (agent logs)      │  │ TrailCompiler │  worklog entries -> steps │
                    │  └──────┬───────┘                           │
                    │         v                                   │
                    │  ┌──────────────┐  ┌────────────────────┐   │
                    │  │TrailProgram  |->│  TrailEncoder      │   │
                    │  │(step list)   |  │  steps -> bytecode  │   │
                    │  └──────────────┘  └────────┬───────────┘   │
                    │                             v               │
                    │                       ┌────────────┐        │
                    │  .bin file ◄────────── │ FLUX Byte  │        │
                    │                       │   Code     │        │
                    │                       └─────┬──────┘        │
                    │                             v               │
                    │  ┌──────────────┐  ┌────────────────────┐   │
                    │  │TrailDecoder  |<-│  TrailDecoder      │   │
                    │  └──────┬───────┘  └────────────────────┘   │
                    │         v                                   │
                    │  ┌──────────────────────────────────────┐   │
                    │  │         TrailExecutor                 │   │
                    │  │  ┌─────────────────────────────┐     │   │
                    │  │  │     WorldInterface           │     │   │
                    │  │  │  ┌───────────┐ ┌──────────┐ │     │   │
                    │  │  │  │ MockWorld │ │ FileWorld│ │     │   │
                    │  │  │  │  (tests)  │ │ (real I/O)│ │     │   │
                    │  │  │  └───────────┘ └──────────┘ │     │   │
                    │  │  └─────────────────────────────┘     │   │
                    │  └──────────────┬───────────────────────┘   │
                    │                 v                            │
                    │          ┌────────────┐                      │
                    │          │TrailResult │                      │
                    │          │ + events   │                      │
                    │          │ + proof    │                      │
                    │          │ + meta-    │                      │
                    │          │   trail    │                      │
                    │          └────────────┘                      │
                    └─────────────────────────────────────────────┘
```

### Module Map

| Module | Key Classes | Responsibility |
|--------|-------------|---------------|
| `trail_codec.py` | `TrailOpcodes`, `TrailStep`, `TrailProgram`, `TrailEncoder`, `TrailDecoder`, `TrailPrinter`, `TrailVerifier` | Core data model, encoding, decoding, printing, and verification |
| `trail_compiler.py` | `TrailCompiler` | Converts worklog entries (dicts) into `TrailProgram` objects |
| `trail_executor.py` | `TrailExecutor`, `MockWorld`, `FileWorld`, `TrailEvent`, `TrailResult`, `WorldInterface` | Replays bytecode against a sandboxed world interface |
| `cli.py` | `main()`, 8 command handlers | CLI entry point with subcommands |

### How It Works

1. **Compile**: The `TrailCompiler` converts structured worklog entries (Python dicts with an `op` key) into a `TrailProgram` — an ordered list of `TrailStep` objects. Each step contains an opcode, operands (encoded as compact u16 hash references), and optional metadata.

2. **Encode**: The `TrailEncoder` serializes a `TrailProgram` into compact FLUX bytecode. String operands are hashed to 4-byte SHA-256 prefixes and stored in a trailing hash table, making the bytecode self-contained and compact. The format supports TRAIL_BEGIN/END framing, per-step opcodes with variable-length operands, and an append-only string hash table for round-trip fidelity.

3. **Execute**: The `TrailExecutor` replays compiled bytecode against a `WorldInterface`. Every operation is dispatched through the interface — file reads/writes go to the filesystem, git operations invoke subprocesses, and inter-agent messages go to the bottle system. Each step emits a `TrailEvent` with a SHA-256 proof hash, and the executor produces a `TrailResult` containing the full execution trail and a cryptographic fingerprint.

### Opcode Map

Trail-FLUX opcodes are organized into three ranges:

| Range | Category | Count | Purpose |
|-------|----------|-------|---------|
| `0x90-0x9F` | Trail Operations | 16 | File I/O, git, messaging, trust, control flow |
| `0xA0-0xA3` | Meta Operations | 4 | Trail framing, comments, labels |
| `0xB0+` | Markers | 1 | String hash table section |

**Trail Operations**: `GIT_COMMIT`, `GIT_PUSH`, `FILE_READ`, `FILE_WRITE`, `FILE_EDIT`, `TEST_RUN`, `SEARCH_CODE`, `BOTTLE_DROP`, `BOTTLE_READ`, `LEVEL_UP`, `SPELL_CAST`, `ROOM_ENTER`, `TRUST_UPDATE`, `CAP_ISSUE`, `BRANCH`, `NOP`

**Meta Operations**: `TRAIL_BEGIN`, `TRAIL_END`, `COMMENT`, `LABEL`

### Bytecode Format

```
[0xA0] [agent_id: u8] [trail_id: 4 bytes] [timestamp: 4 bytes]  -> TRAIL_BEGIN
[opcode: u8] [operand_count: u8] [operands: variable u16...]        -> each step
[0xA1] [total_steps: u16] [status: u8]                          -> TRAIL_END
[0xB0] [table_length: u16]
  [hash: 8 bytes] [string_length: u8] [string_bytes: variable]   -> STRING TABLE
```

## Tracing Model

### Trace Lifecycle

Every trail follows a well-defined lifecycle from creation to verified execution:

```
  CREATE            COMPILE            ENCODE             EXECUTE             VERIFY
  (worklog)   ->    (TrailProgram) ->  (FLUX binary)  ->  (TrailResult)   ->  (6-pass check)
      |                 |                  |                 |
  agent actions     validated steps    compact bytes    events + proof    integrity proof
  dict entries      opcode + args     hash table        fingerprint       all clean?
```

### Span Model: Steps as Distributed Spans

Each `TrailStep` in a trail functions as a **distributed span** — a named, timed unit of work:

```
TrailProgram
├── TRAIL_BEGIN          <- root span (agent_id, trail_id, timestamp)
│   ├── FILE_READ        <- child span (path operand)
│   │   └── proof: a1b2c3d4e5f6...
│   ├── FILE_EDIT        <- child span (path, old, new operands)
│   │   └── proof: f7e8d9c0b1a2...
│   ├── TEST_RUN         <- child span (test_path, expected_count)
│   │   └── proof: 3a4b5c6d7e8f...
│   └── TRAIL_END        <- closing span (total_steps, status)
│
├── String Hash Table    <- append-only, enables operand resolution
└── Fingerprint          <- SHA-256 of entire bytecode
```

Key properties of the span model:
- **Ordered**: Steps execute sequentially, preserving causal ordering
- **Typed**: Each opcode defines its operand signature (`s` for string/u16 pairs, `n` for numeric/u16)
- **Self-describing**: The trailing hash table lets any decoder resolve operand references
- **Composable**: Two valid trails can be concatenated via `TrailProgram.concatenate()`

### Execution Proof Chain

When a trail is executed, each step produces a `TrailEvent` with a cryptographic proof:

```
TrailEvent
├── step_index: int          <- position in the trail
├── opcode: TrailOpcodes     <- which operation
├── operands: dict           <- resolved named arguments
├── result: str              <- human-readable outcome
├── duration_ms: float       <- wall-clock timing
├── timestamp: float         <- when it happened
└── proof: str               <- SHA-256(step:opcode:result)[:16]
```

The executor chains these proofs into an **execution meta-trail** — a new FLUX bytecode recording what happened during execution:

```
Original Trail                    Execution Meta-Trail
┌─────────────────┐              ┌──────────────────────┐
│ TRAIL_BEGIN     │              │ TRAIL_BEGIN          │
│ FILE_READ       │   execute    │ COMMENT: exec started│
│ FILE_WRITE      │ ──────────>  │ COMMENT: FILE_READ ok│
│ TRAIL_END       │              │ COMMENT: FILE_WRITE ok│
└─────────────────┘              │ TRAIL_END            │
                                 └──────────────────────┘
                                        |
                                 execution_fingerprint
                                 (SHA-256 of meta-trail)
```

### Verification Model

The `TrailVerifier` runs six independent integrity checks:

| # | Check | What It Validates |
|---|-------|-------------------|
| 1 | Structural | Trail has valid `TRAIL_BEGIN` / `TRAIL_END` framing |
| 2 | Opcode | All opcodes are valid Trail-FLUX values |
| 3 | Operand count | Each step has the expected number of u16 operands |
| 4 | Round-trip | `encode -> decode` produces identical opcodes and operands |
| 5 | Fingerprint | Same trail always produces the same SHA-256 hash |
| 6 | Hash table | All referenced string hashes exist in the table |

### WorldInterface Sandbox

Execution is fully sandboxed behind the `WorldInterface` protocol. Two implementations are provided:

```
           WorldInterface (Protocol)
          ┌──────────────────────────────┐
          │  git_commit(repo, message)   │
          │  git_push(repo)              │
          │  file_read(path)             │
          │  file_write(path, content)   │
          │  file_edit(path, old, new)   │
          │  test_run(test_path, count)  │
          │  search_code(pattern)        │
          │  bottle_drop(target, content)│
          │  bottle_read(source)         │
          │  level_up(agent, level)      │
          │  spell_cast(spell)           │
          │  room_enter(room)            │
          │  trust_update(target, delta) │
          │  cap_issue(action, holder)   │
          └──────┬────────────┬──────────┘
                 |            |
      ┌──────────▼──┐  ┌─────▼──────────┐
      │  MockWorld   │  │  FileWorld      │
      │  - records   │  │  - real files   │
      │  - no side   │  │  - git subprocess│
      │    effects   │  │  - pytest        │
      │  - simulated │  │  - ripgrep       │
      │    failures  │  │  - auto-backup   │
      └─────────────┘  └─────────────────┘
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

### Step-by-Step Execution with Pause/Resume

```python
from trail_executor import TrailExecutor, MockWorld

executor = TrailExecutor(world=MockWorld(), bytecode=bytecode)

# Execute one step at a time
while True:
    event = executor.step()
    if event is None:
        break
    print(f"  #{event.step_index} {event.opcode.name}: {event.result}")

# Or pause mid-execution and resume later
executor.pause()
# ... do something else ...
executor.resume()
result = executor.execute()
```

### Trail Concatenation

```python
from trail_codec import TrailStep, TrailOpcodes

trail_a = compiler.compile([{"op": "TRAIL_BEGIN", "agent": "bot-a", "trail_id": "part-a", "ts": 1000},
                             {"op": "FILE_READ", "path": "a.py"},
                             {"op": "TRAIL_END", "steps": 1, "status": 0}])

trail_b = compiler.compile([{"op": "TRAIL_BEGIN", "agent": "bot-b", "trail_id": "part-b", "ts": 2000},
                             {"op": "FILE_WRITE", "path": "b.py", "content": "data"},
                             {"op": "TRAIL_END", "steps": 1, "status": 0}])

merged = trail_a.concatenate(trail_b)
# merged.steps: [TRAIL_BEGIN, FILE_READ, FILE_WRITE, TRAIL_END]
print(f"Merged fingerprint: {merged.fingerprint()[:32]}...")
```

## Integration

### With the Cocapn Fleet

The trail-agent integrates with other fleet agents through the bottle messaging system (`BOTTLE_DROP` / `BOTTLE_READ` opcodes). Agents can share executable trail programs by dropping them into fleet message channels, enabling replay and verification of work across the fleet.

### Implementing a Custom WorldInterface

The `WorldInterface` is a Python `Protocol` — any class implementing the required methods can serve as an execution backend:

```python
from trail_executor import WorldInterface, TrailExecutor

class RemoteWorld:
    """Execute trail operations against a remote API."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def file_read(self, path: str) -> str:
        # POST to remote API, return result
        return f"remote_file_read: {path}"

    def file_write(self, path: str, content: str) -> str:
        # POST to remote API
        return f"remote_file_write: {path}"

    def git_commit(self, repo: str, message: str) -> str:
        return f"remote_git_commit: {message}"

    def git_push(self, repo: str) -> str:
        return f"remote_git_push: {repo}"

    def test_run(self, test_path: str, expected: int) -> str:
        return f"remote_test_run: {test_path}"

    def search_code(self, pattern: str) -> str:
        return f"remote_search_code: {pattern}"

    def bottle_drop(self, target: str, content: str) -> str:
        return f"remote_bottle_drop: ->{target}"

    def bottle_read(self, source: str) -> str:
        return f"remote_bottle_read: <-{source}"

    def level_up(self, agent: str, level: int) -> str:
        return f"remote_level_up: {agent} -> {level}"

    def spell_cast(self, spell: str) -> str:
        return f"remote_spell_cast: {spell}"

    def room_enter(self, room: str) -> str:
        return f"remote_room_enter: {room}"

    def trust_update(self, target: str, delta: float) -> str:
        return f"remote_trust_update: {target} {delta:+.1f}"

    def cap_issue(self, action: str, holder: str) -> str:
        return f"remote_cap_issue: {action} -> {holder}"


# Use it
world = RemoteWorld(base_url="https://fleet.example.com/api")
executor = TrailExecutor(world=world, bytecode=bytecode)
result = executor.execute()
print(result.summary())
```

### Workshop Structure

The `workshop/` directory contains trail recipes and exercises:

- `recipes/hot/` — Immediate-use trail recipes (git workflows, file operations)
- `recipes/med/` — Multi-step composable trail recipes (trust communication)
- `recipes/cold/` — Advanced composable trail patterns
- `bootcamp/` — Learning exercises (hello trail, round trip)
- `dojo/` — Mastery challenges (string concatenation)

### Embedding in Other Agents

Trail-agent is designed to be embedded as a library. Import and use directly:

```python
# In your agent's code
import sys, os
sys.path.insert(0, "/path/to/trail-agent")

from trail_compiler import TrailCompiler
from trail_codec import TrailEncoder, TrailVerifier

# Build a trail from your agent's actions
compiler = TrailCompiler()
entries = [
    {"op": "TRAIL_BEGIN", "agent": "my-agent", "trail_id": "session-42", "ts": 1700000000},
    {"op": "FILE_READ", "path": "config.yaml", "desc": "Read configuration"},
    {"op": "SEARCH_CODE", "pattern": "class Handler", "desc": "Find handler class"},
    {"op": "FILE_EDIT", "path": "handler.py", "old": "pass", "new": "return True",
     "desc": "Implement handler"},
    {"op": "TEST_RUN", "test_path": "tests/", "count": 15, "desc": "Run tests"},
    {"op": "TRAIL_END", "steps": 4, "status": 0, "desc": "Session complete"},
]
bytecode = compiler.compile_and_encode(entries)

# Verify before sharing
verifier = TrailVerifier()
assert verifier.verify_bytecode(bytecode), "Trail verification failed"

# Share the bytecode with other agents via bottles
share_with_fleet("session-42.bin", bytecode)
```

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
