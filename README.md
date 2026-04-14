# trail-agent

**Python agent for executing trails – part of the Cocapn Fleet**  
<https://github.com/SuperInstance>

## Description
`trail-agent` provides a lightweight, extensible framework to compile, encode, and execute trail definitions. It integrates with the Cocapn Fleet and includes a CLI, core modules, and a test suite.

## Installation
```bash
git clone https://github.com/SuperInstance/trail-agent.git
cd trail-agent
pip install -e .
```

## Usage
Run the command‑line interface:
```bash
python -m trail_agent.cli --help
# or
python -m trail_agent --run path/to/trail.yaml
```

Typical workflow:
1. **Compile** a trail – `trail_compiler.py`
2. **Encode** it – `trail_codec.py`
3. **Execute** – `trail_executor.py`

## Related
- **Cocapn Fleet** – the larger ecosystem: <https://github.com/SuperInstance>
- **Documentation & Wiki** – <https://github.com/SuperInstance/trail-agent/wiki>
- **Other agents** – see the `workshop/` directory for examples and extensions.