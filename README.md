# Self-Evolving Capability-Based AI Agent

An AI agent that starts with minimal tools and **grows its own library** of reusable "capability tools." For each task, the agent searches its capability registry and chooses one of four operations: **REUSE**, **COMPOSE**, **ADAPT**, or **CREATE**. The core architecture never gets more complex as capabilities grow — new abilities are new folders in the tool codebase, never new code in the agent or harness.

## Architecture

Three independent blocks, loosely coupled:

```
┌─────────────┐     execute(name, args)      ┌─────────────┐
│   Agent     │ ◄──────────────────────────► │   Harness   │
│ (agent_loop) │                              │ (harness.py) │
└──────┬──────┘                               └──────┬──────┘
       │                                             │
       │  JSON protocol only                         │  Sandbox (subprocess)
       │  (no native tool calling)                   │  + Registry (atomic)
       │                                             │
┌──────┴──────┐                               ┌──────┴──────┐
│    LLM      │                               │   Tools     │
│ (response)  │                               │ codebase/   │
└─────────────┘                               └─────────────┘
```

1. **LLM + Agent** — Reasoning, planning, capability assessment. Produces JSON actions only.
2. **Harness** — The ONLY component that touches the tool codebase. Handles discovery, execution, validation, versioning, health tracking, and registry updates.
3. **Tool Codebase** — Versioned tool folders, manifests, and `registry.json`. Written only by the harness.

## Key Features

### Step 1: Versioned Tool Storage + Registry
- Tools stored as `tools/<name>/<semver>/` with immutable versions
- Atomic `tools/registry.json` with per-tool stats, health, and quarantine flags
- Auto-migration from flat `tools/<name>/` layout to `tools/<name>/1.0.0/`
- `search_capabilities(query)` — keyword search over name, tags, description, and examples

### Step 2: 6-Stage Validation Pipeline
Every submitted tool passes through:
1. **Manifest Lint** — required fields, semver, JSON Schema validation
2. **Static Checks** — tree-sitter-based code parsing for import/call analysis against declared permissions
3. **Example Tests** — sandbox execution + determinism check
4. **Regression Tests** — backward compatibility for same-MAJOR versions
5. **Permission Escalation** — blocks new permissions without approval
6. **Promote** — installs as immutable version with duplicate warnings

### Step 3: Health Tracking, Fallback & Quarantine
- Input/output schema validation before and after each call
- Automatic fallback to healthy older versions on crash
- Quarantine after 3 consecutive failures (hidden from search)
- `rollback_tool()` and `run_code()` core tools for recovery

### Step 4: Four Operations + Workflow Composition
- **REUSE** — existing tool fits the task
- **COMPOSE** — chain tools via workflow manifests
- **ADAPT** — create a higher version of an existing tool
- **CREATE** — build a new tool from scratch
- Workflow tools use `kind: "workflow"` with step references (`$input.<field>`, `$steps.<id>.<field>`)

### Step 5: Sandbox Hardening
- Windows-safe subprocess sandbox with `SYSTEMROOT` env handling
- Optional Docker backend (documented, plain subprocess is default)
- Defense-in-depth: tools never imported into harness process

## Quick Start

### Prerequisites
- Python 3.12
- Windows 11 (POSIX-only APIs are not used)

### Installation
```bash
# Clone the repository
git clone https://github.com/KavyanshSharma09/self-evolving-agent.git
cd self-evolving-agent

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install openai python-dotenv jsonschema pytest tree-sitter tree-sitter-python
```

### Configuration
Create a `.env` file in the project root:
```
API_KEY=your_api_key_here
BASE_URL=your_api_base_url_here
```

### Running the Agent
```bash
# Run with a specific task
python run_agent_dynamic.py "Your task description here"

# Run the evaluation script
python eval.py

# Run the test suite
pytest test_step1.py test_steps2to5.py -v
```

## Project Structure

```
self-evolving-agent/
├── agent_loop.py           # Main agent loop: ask model → parse → execute → repeat
├── harness.py              # Core: versioned storage, registry, validation, health tracking
├── sandbox.py              # Subprocess sandbox for tool execution
├── protocol.py             # JSON protocol parser and system prompt builder
├── response.py             # LLM client (OpenAI-compatible)
├── eval.py                 # Evaluation script with ~10 tasks
├── logs/                   # Validation logs (JSON lines, auto-generated)
│   └── validation.json     # Every submit_tool run with stages and outcomes
├── run_agent_dynamic.py    # Entry point for running the agent
├── capability_registry.py  # (Deprecated) absorbed into harness.py
├── tools/
│   ├── registry.json       # Atomic registry of all tools
│   └── create_tool/
│       └── 1.0.0/          # Versioned tool storage
│           ├── manifest.json
│           └── main.py
├── workspace/              # Agent's working directory
├── test_step1.py           # Tests for Step 1 (36 tests)
├── test_steps2to5.py       # Tests for Steps 2-5 (19 tests)
└── .gitignore
```

## Tool Format

Each tool consists of a `manifest.json` and `main.py`:

**manifest.json:**
```json
{
  "name": "add",
  "version": "1.0.0",
  "description": "Adds two numbers together.",
  "input_schema": {
    "type": "object",
    "properties": {
      "a": {"type": "number"},
      "b": {"type": "number"}
    },
    "required": ["a", "b"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "result": {"type": "number"}
    }
  },
  "examples": [
    {"input": {"a": 1, "b": 2}, "output": {"result": 3}}
  ],
  "permissions": {}
}
```

**main.py:**
```python
import sys, json

def main(args):
    return {"result": args["a"] + args["b"]}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    result = main(args)
    print(json.dumps(result))
```

## Core Tools

All routed through `execute(name, args)`:

| Tool | Description |
|------|-------------|
| `ws_write(path, content)` | Write file to workspace (path-escape protected) |
| `ws_list(path)` | List workspace directory contents |
| `search_capabilities(query, limit)` | Search the tool registry by keyword |
| `submit_tool(manifest, code)` | Submit a new/updated tool for validation |
| `rollback_tool(name, version)` | Revert a tool to a previous healthy version |
| `run_code(code, timeout)` | Run arbitrary Python in sandbox (always-available fallback) |

## Testing

The test suite uses a scripted fake LLM — no API key required:

```bash
# Run all tests
pytest test_step1.py test_steps2to5.py -v

# Run specific test files
pytest test_step1.py -v          # Step 1: 36 tests
pytest test_steps2to5.py -v      # Steps 2-5: 19 tests
```

### Test Coverage
- Create → Search → Reuse workflow
- Adapt with backward compatibility
- Static check rejection (undeclared imports)
- Non-deterministic tool rejection
- Workflow composition
- Runtime fallback to previous versions
- Quarantine after 3 failures
- Bad input doesn't hurt health
- Permission escalation handling
- Workspace path escape protection
- Timeout handling for infinite loops
- `execute()` never raises on any input
- Registry stays valid JSON after failures

## Evaluation

Run `eval.py` to measure the agent's performance across 10 related tasks:

```bash
python eval.py
```

**Key metrics:**
- Operation used per task (CREATE/REUSE/COMPOSE/ADAPT/run_code)
- Tools created and reused
- Rejected submissions
- Fallbacks triggered
- **Reuse rate** — how often the agent reuses instead of recreating

## Validation Logging

Every `submit_tool` call is automatically logged to `logs/validation.json` as JSON lines
(one JSON object per line). Each entry contains:

- **timestamp** — when the validation ran (UTC)
- **tool** — tool name
- **version** — submitted version
- **status** — `accepted`, `rejected`, or `needs_approval`
- **dry_run** — whether this was a dry run
- **stages** — all 6 validation stages with their pass/fail status and details

This makes it easy to audit why a tool was rejected, track validation trends, and debug
agent behavior. The log directory is excluded from git (`.gitignore`).

```bash
# View recent validation logs
tail -n 20 logs/validation.json | python -m json.tool
```

## Design Principles

1. **`execute(name, args)` never raises** — every failure returns a structured dict
2. **Tools are never imported** — they only run via the sandbox
3. **Registry stays valid JSON** — atomic writes via `os.replace()`
4. **Immutable versions** — never edit an installed version in place
5. **Harness interface doesn't change** — new tools don't require harness modifications
6. **No `ws_read` core tool** — the agent must create its own file-reading tool

## Security Notes

### Workspace Confinement

All tools and `run_code` snippets are **confined to the workspace directory**. The
sandbox wrapper intercepts `open()`, `os.open()`, and `pathlib.Path` file operations,
raising `PermissionError` if any path resolves outside the workspace. This prevents
tools from reading sensitive files like `.env` or system files.

### Subprocess Sandbox

The plain `subprocess.run()` sandbox is **defense-in-depth**, not a real security
boundary. It provides timeout protection, process isolation, and workspace confinement,
but does not prevent network access or other system calls. For stronger isolation,
the optional Docker backend can be implemented.

## License

This project is provided as-is for educational and research purposes.
