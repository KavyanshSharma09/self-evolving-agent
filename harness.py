"""
Harness — discovers tools, builds the system prompt, and runs tools safely.
All tools live under the single ./tools/ directory with versioned storage:
    tools/<name>/<semver>/manifest.json + main.py
Registry at tools/registry.json is written atomically.
"""

import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import datetime
from sandbox import run_tool, run_raw

# ---------------------------------------------------------------------------
# Tree-sitter for code parsing (replaces ast)
# ---------------------------------------------------------------------------

try:
    from tree_sitter import Language, Parser
    import tree_sitter_python as tspython
    _PY_LANGUAGE = Language(tspython.language())
    _TS_PARSER = Parser(_PY_LANGUAGE)
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False
    _TS_PARSER = None

# ---------------------------------------------------------------------------
# JSON logging for validation observability
# ---------------------------------------------------------------------------

_LOG_DIR = pathlib.Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "validation.json"


def _log_validation(event: dict) -> None:
    """Append a JSON line to the validation log file for observability."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        **event,
    }
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# Directory where all tools reside
TOOLS_DIR = pathlib.Path(__file__).parent / "tools"
REGISTRY_PATH = TOOLS_DIR / "registry.json"
WORKSPACE_DIR = pathlib.Path(__file__).parent / "workspace"

# ---------------------------------------------------------------------------
# Semver helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver_parse(version_str: str) -> tuple:
    """Parse 'X.Y.Z' into (X, Y, Z) tuple. Raises ValueError on bad format."""
    m = _SEMVER_RE.match(version_str)
    if not m:
        raise ValueError(f"Invalid semver: {version_str}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _semver_compare(v1: str, v2: str) -> int:
    """Compare two semver strings. Returns -1, 0, or 1."""
    t1 = _semver_parse(v1)
    t2 = _semver_parse(v2)
    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Registry — atomic read/write
# ---------------------------------------------------------------------------

def _default_registry_entry():
    """Return a default registry entry for a new tool."""
    return {
        "active": None,
        "versions": {},
        "stats": {
            "calls": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "last_used": None,
        },
        "quarantined": False,
    }


def load_registry() -> dict:
    """Load the tool registry from tools/registry.json. Returns empty dict if missing."""
    if REGISTRY_PATH.is_file():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_registry(registry: dict) -> None:
    """Atomically save the registry to tools/registry.json."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(REGISTRY_PATH))


# ---------------------------------------------------------------------------
# Migration — flat tools/<name>/ → tools/<name>/1.0.0/
# ---------------------------------------------------------------------------

def _migrate_flat_tools(registry: dict) -> dict:
    """Detect flat tools/<name>/ layout and migrate to versioned structure.
    Returns the updated registry."""
    if not TOOLS_DIR.is_dir():
        return registry

    for folder in sorted(TOOLS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        name = folder.name
        # Skip registry.json, non-directories, and already-versioned folders
        if name.startswith("_") or name == "registry.json":
            continue
        manifest_path = folder / "manifest.json"
        entry_path = folder / "main.py"
        if not (manifest_path.is_file() and entry_path.is_file()):
            continue
        # Check if already versioned (has subdirectories with semver names)
        has_versions = any(
            sub.is_dir() and _SEMVER_RE.match(sub.name)
            for sub in folder.iterdir()
        )
        if has_versions:
            continue
        # This is a flat tool — migrate it
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"Warning: skipping tool '{name}' — invalid manifest")
            continue

        tool_name = manifest.get("name", name)
        version_dir = folder / "1.0.0"
        version_dir.mkdir(parents=True, exist_ok=True)

        # Enrich manifest with versioned fields
        enriched = {
            "name": tool_name,
            "version": "1.0.0",
            "description": manifest.get("description", ""),
            "input_schema": manifest.get(
                "input_schema",
                {"type": "object", "properties": manifest.get("args", {})},
            ),
            "output_schema": manifest.get("output_schema", {"type": "object"}),
            "examples": manifest.get("examples", []),
            "permissions": manifest.get("permissions", {}),
            "tags": manifest.get("tags", []),
        }
        (version_dir / "manifest.json").write_text(
            json.dumps(enriched, indent=2), encoding="utf-8"
        )
        shutil.move(str(entry_path), str(version_dir / "main.py"))

        # Update registry
        if tool_name not in registry:
            registry[tool_name] = _default_registry_entry()
        entry = registry[tool_name]
        entry["active"] = "1.0.0"
        entry["versions"]["1.0.0"] = {
            "description": enriched["description"],
            "tags": enriched.get("tags", []),
            "permissions": enriched.get("permissions", {}),
            "healthy": True,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }

        # The version_dir is inside the old flat folder. Move it to the correct
        # location under TOOLS_DIR, then remove the old flat folder.
        final_dir = TOOLS_DIR / tool_name
        if final_dir.exists():
            # Already exists — merge versions
            pass
        else:
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(version_dir), str(final_dir / "1.0.0"))

        # Remove the old flat folder (now empty or nearly empty)
        try:
            # Clean up leftover manifest.json in the flat folder
            leftover_manifest = folder / "manifest.json"
            if leftover_manifest.is_file():
                leftover_manifest.unlink()
            # Remove the empty folder
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

        print(f"Migrated tool '{tool_name}' to versioned layout (1.0.0)")

    return registry


# ---------------------------------------------------------------------------
# Scanning — versioned tool discovery
# ---------------------------------------------------------------------------

def _scan_versioned(registry: dict) -> dict:
    """Return a dict mapping tool name → manifest for every active, non-quarantined tool.
    The manifest is enriched with '_entry' (path to main.py) and '_version'."""
    tools = {}
    if not TOOLS_DIR.is_dir():
        return tools

    for tool_name, entry in registry.items():
        if entry.get("quarantined"):
            continue
        active_version = entry.get("active")
        if not active_version:
            continue
        version_dir = TOOLS_DIR / tool_name / active_version
        manifest_path = version_dir / "manifest.json"
        entry_path = version_dir / "main.py"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("kind") == "workflow":
                    # Workflow tools have no main.py
                    manifest["_entry"] = None
                elif entry_path.is_file():
                    manifest["_entry"] = str(entry_path)
                else:
                    continue
                manifest["_version"] = active_version
                manifest["_tool_name"] = tool_name
                tools[tool_name] = manifest
            except (json.JSONDecodeError, KeyError):
                pass

    return tools


# ---------------------------------------------------------------------------
# Search capabilities
# ---------------------------------------------------------------------------

def search_capabilities(query: str, limit: int = 5) -> list:
    """Keyword search over name, tags, description, and example text.
    Returns matching tools with name, version, description, args schema, and one example.
    Hides quarantined tools."""
    registry = load_registry()
    query_lower = query.lower()
    results = []

    for tool_name, entry in registry.items():
        if entry.get("quarantined"):
            continue
        active_version = entry.get("active")
        if not active_version:
            continue
        version_info = entry.get("versions", {}).get(active_version, {})
        version_dir = TOOLS_DIR / tool_name / active_version
        manifest_path = version_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Search in name, tags, description, and example text
        searchable = (
            tool_name
            + " "
            + " ".join(version_info.get("tags", []))
            + " "
            + manifest.get("description", "")
            + " "
            + json.dumps(manifest.get("examples", []))
        ).lower()

        # Simple keyword matching: any word in query matches
        query_words = query_lower.split()
        if any(word in searchable for word in query_words):
            result = {
                "name": tool_name,
                "version": active_version,
                "description": manifest.get("description", ""),
                "input_schema": manifest.get("input_schema", {}),
                "output_schema": manifest.get("output_schema", {}),
                "tags": version_info.get("tags", []),
            }
            examples = manifest.get("examples", [])
            if examples:
                result["example"] = examples[0]
            results.append(result)

    # Sort by relevance (name match first, then description match)
    results.sort(
        key=lambda r: (
            0 if query_lower in r["name"].lower() else 1,
            0 if query_lower in r["description"].lower() else 1,
        )
    )

    return results[:limit]


# ---------------------------------------------------------------------------
# Human-readable description for the system prompt
# ---------------------------------------------------------------------------

def describe() -> str:
    """Return a short description of available core tools and how to discover capabilities."""
    registry = load_registry()
    tools = _scan_versioned(registry)
    if not tools:
        return "(no installed capabilities — use search_capabilities to discover tools)"
    lines = []
    for name, m in tools.items():
        version = m.get("_version", "unknown")
        desc = m.get("description", "")
        lines.append(f"- {name} (v{version}): {desc}")
    return "\n".join(lines)


def list_available_tools() -> list:
    """Return a structured list of all available tools: core tools + installed capabilities.
    Each entry contains name, type (core/capability), version, description, and input_schema."""
    result = []
    # Core tools — always available
    core_tools = [
        {"name": "ws_write", "type": "core", "version": "built-in",
         "description": "Write content to a file in the workspace.",
         "input_schema": {"path": "string", "content": "string"}},
        {"name": "ws_list", "type": "core", "version": "built-in",
         "description": "List files in a workspace directory.",
         "input_schema": {"path": "string (optional)"}},
        {"name": "search_capabilities", "type": "core", "version": "built-in",
         "description": "Search for installed tools by keyword.",
         "input_schema": {"query": "string", "limit": "int (default 5)"}},
        {"name": "submit_tool", "type": "core", "version": "built-in",
         "description": "Submit a new or updated tool for validation and installation.",
         "input_schema": {"manifest": "object", "code": "string"}},
        {"name": "rollback_tool", "type": "core", "version": "built-in",
         "description": "Roll back a tool to a previous healthy version.",
         "input_schema": {"name": "string", "version": "string (optional)"}},
        {"name": "run_code", "type": "core", "version": "built-in",
         "description": "Run a Python snippet in the sandbox (always-available fallback).",
         "input_schema": {"code": "string", "timeout": "float (default 10)"}},
        {"name": "list_available_tools", "type": "core", "version": "built-in",
         "description": "List all available tools (core + installed capabilities) as JSON.",
         "input_schema": {}},
    ]
    result.extend(core_tools)
    # Installed capabilities from registry
    registry = load_registry()
    tools = _scan_versioned(registry)
    for name, m in tools.items():
        result.append({
            "name": name,
            "type": "capability",
            "version": m.get("_version", "unknown"),
            "description": m.get("description", ""),
            "input_schema": m.get("input_schema", {}),
        })
    return result


# ---------------------------------------------------------------------------
# Workspace core tools
# ---------------------------------------------------------------------------

def _ws_write(path: str, content: str) -> dict:
    """Write content to a file in the workspace. Path must be relative and safe."""
    try:
        target = (WORKSPACE_DIR / path).resolve()
        if not str(target).startswith(str(WORKSPACE_DIR.resolve())):
            return {"error": "path_escape", "detail": f"Path '{path}' escapes workspace"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"result": f"wrote {len(content)} bytes to {path}"}
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)}


def _ws_list(path: str = "") -> dict:
    """List files in a workspace directory. Path must be relative and safe."""
    try:
        target = (WORKSPACE_DIR / path).resolve()
        if not str(target).startswith(str(WORKSPACE_DIR.resolve())):
            return {"error": "path_escape", "detail": f"Path '{path}' escapes workspace"}
        if not target.is_dir():
            return {"error": "not_a_directory", "detail": str(target)}
        entries = [p.name for p in target.iterdir()]
        return {"result": entries}
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)}


# ---------------------------------------------------------------------------
# Run code core tool
# ---------------------------------------------------------------------------

def _run_code(code: str, timeout: float = 10.0) -> dict:
    """Run a Python snippet in the sandbox with workspace as cwd.
    Uses run_raw so plain print() output works, not just JSON."""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    script = tmp_dir / "snippet.py"
    script.write_text(code, encoding="utf-8")
    try:
        result = run_raw(str(script), timeout=timeout, cwd=str(WORKSPACE_DIR))
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Rollback tool
# ---------------------------------------------------------------------------

def _rollback_tool(name: str, version: str = None) -> dict:
    """Revert a tool to a previous healthy version. Clears quarantine."""
    registry = load_registry()
    if name not in registry:
        return {"error": "not_found", "detail": f"Tool '{name}' not in registry"}

    entry = registry[name]
    available = [
        v for v, info in entry.get("versions", {}).items() if info.get("healthy")
    ]
    if not available:
        return {"error": "no_healthy_version", "detail": f"No healthy version of '{name}'"}

    if version and version in available:
        target = version
    else:
        # Pick the newest healthy version
        target = sorted(available, key=lambda v: _semver_parse(v), reverse=True)[0]

    entry["active"] = target
    entry["quarantined"] = False
    entry["stats"]["consecutive_failures"] = 0
    save_registry(registry)
    return {"result": f"Rolled back '{name}' to version {target}"}


# ---------------------------------------------------------------------------
# Call tool — with health tracking, fallback, and quarantine
# ---------------------------------------------------------------------------

def call_tool(name: str, args: dict) -> dict:
    """Run a tool by name via the sandbox with health tracking.
    On failure: marks version unhealthy, tries fallback, quarantines after 3 consecutive failures.
    Returns a dict with 'result' or 'error' keys. Never raises."""
    registry = load_registry()

    # Check quarantine
    if name in registry and registry[name].get("quarantined"):
        return {"error": "quarantined", "detail": f"Tool '{name}' is quarantined"}

    tools = _scan_versioned(registry)
    if name not in tools:
        return {"error": "not_found", "detail": f"Tool '{name}' not found"}

    manifest = tools[name]
    tool_entry = registry.get(name, {})
    is_workflow = manifest.get("kind") == "workflow"

    if is_workflow:
        return execute_workflow(manifest, args)

    entry_path = manifest.get("_entry")
    if entry_path is None:
        return {"error": "no_entry", "detail": f"Tool '{name}' has no entry point"}

    # Validate input against input_schema (bad input doesn't count against health)
    err = _schema_error(args, manifest.get("input_schema", {}))
    if err:
        return {"error": "invalid_input", "detail": err}

    active_version = manifest.get("_version", "")
    result = run_tool(entry_path, args, cwd=str(WORKSPACE_DIR))

    if result["ok"]:
        output = result["output"]
        # Tool-reported errors don't count as failures
        if isinstance(output, dict) and "error" in output:
            # Update stats but don't mark unhealthy
            _update_stats(registry, name, success=True)
            save_registry(registry)
            return {"result": output}
        # Validate output against output_schema
        err = _schema_error(output, manifest.get("output_schema", {}))
        if err:
            _record_failure(registry, name, active_version)
            save_registry(registry)
            return {"error": "invalid_output", "detail": f"Output does not match output_schema: {err}"}
        # Success
        _update_stats(registry, name, success=True)
        save_registry(registry)
        return {"result": output}

    # Sandbox error — mark version unhealthy and try fallback
    _record_failure(registry, name, active_version)

    # Try fallback to a healthy older version
    fallback_result = _try_fallback(registry, name, args)
    if fallback_result is not None:
        save_registry(registry)
        if "note" in fallback_result:
            return fallback_result
        return fallback_result

    # No fallback available
    save_registry(registry)
    return {"error": result.get("error", "unknown"), "detail": result.get("detail", "")}


def _update_stats(registry: dict, name: str, success: bool) -> None:
    """Update call stats for a tool."""
    if name not in registry:
        return
    stats = registry[name].get("stats", {})
    stats["calls"] = stats.get("calls", 0) + 1
    stats["last_used"] = datetime.datetime.utcnow().isoformat() + "Z"
    if success:
        stats["consecutive_failures"] = 0


def _record_failure(registry: dict, name: str, version: str) -> None:
    """Record a failure: mark version unhealthy, update stats, check quarantine."""
    if name not in registry:
        return
    entry = registry[name]
    stats = entry.get("stats", {})
    stats["failures"] = stats.get("failures", 0) + 1
    stats["consecutive_failures"] = stats.get("consecutive_failures", 0) + 1

    # Mark version unhealthy
    versions = entry.get("versions", {})
    if version in versions:
        versions[version]["healthy"] = False

    # Quarantine after 3 consecutive failures
    if stats["consecutive_failures"] >= 3:
        entry["quarantined"] = True


def _try_fallback(registry: dict, name: str, args: dict) -> dict:
    """Try to fall back to a healthy older version. Returns result dict or None."""
    entry = registry.get(name, {})
    versions = entry.get("versions", {})
    active = entry.get("active", "")

    # Find healthy versions, sorted newest first, excluding the current active
    healthy_versions = [
        v for v, info in versions.items()
        if info.get("healthy") and v != active
    ]
    healthy_versions.sort(key=lambda v: _semver_parse(v), reverse=True)

    for ver in healthy_versions:
        ver_dir = TOOLS_DIR / name / ver
        entry_path = ver_dir / "main.py"
        if not entry_path.is_file():
            continue
        result = run_tool(str(entry_path), args, cwd=str(WORKSPACE_DIR))
        if result["ok"]:
            # Fallback succeeded — make it active
            entry["active"] = ver
            entry["stats"]["consecutive_failures"] = 0
            return {
                "result": result["output"],
                "note": f"Fallback to {name} v{ver} (v{active} was unhealthy)",
            }
        # This fallback also failed — mark it unhealthy too
        if ver in versions:
            versions[ver]["healthy"] = False

    return None


# ---------------------------------------------------------------------------
# Workflow executor
# ---------------------------------------------------------------------------

def execute_workflow(workflow_manifest: dict, input_data: dict) -> dict:
    """Execute a workflow tool by resolving steps and references.
    References: $input.<field> and $steps.<id>.<field>; other strings are literals."""
    steps = workflow_manifest.get("steps", [])
    step_results = {}

    for step in steps:
        step_id = step.get("id")
        tool_name = step.get("tool")
        raw_input = step.get("input", {})

        # Resolve references in input
        resolved_input = _resolve_refs(raw_input, input_data, step_results)

        # Call the tool
        result = call_tool(tool_name, resolved_input)

        if "error" in result:
            return result

        step_results[step_id] = result.get("result", {})

    # Resolve output template
    output_template = workflow_manifest.get("output", {})
    final_output = _resolve_refs(output_template, input_data, step_results)
    return {"result": final_output}


def _resolve_refs(value, input_data, step_results):
    """Recursively resolve $input.<field> and $steps.<id> / $steps.<id>.<field> references."""
    if isinstance(value, str):
        if value.startswith("$input."):
            field = value[len("$input."):]
            return input_data.get(field, value)
        elif value.startswith("$steps."):
            rest = value[len("$steps."):]
            dot_idx = rest.find(".")
            if dot_idx != -1:
                # $steps.<id>.<field> — resolve a specific field
                step_id = rest[:dot_idx]
                field = rest[dot_idx + 1:]
                step_out = step_results.get(step_id, {})
                return step_out.get(field, value)
            else:
                # $steps.<id> — resolve the entire step result
                step_id = rest
                return step_results.get(step_id, value)
        return value
    elif isinstance(value, dict):
        return {k: _resolve_refs(v, input_data, step_results) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_refs(v, input_data, step_results) for v in value]
    return value


# ---------------------------------------------------------------------------
# Validation pipeline — 6 stages
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# JSON Schema validation helpers — safe import to avoid UnboundLocalError
# ---------------------------------------------------------------------------

try:
    from jsonschema import validate as _js_validate, ValidationError as _JSError, Draft7Validator, SchemaError
except ImportError:
    _js_validate, _JSError = None, Exception
    Draft7Validator, SchemaError = None, None


def _schema_error(instance, schema):
    """Return an error message if instance violates schema, else None."""
    if not schema or _js_validate is None:
        return None
    try:
        _js_validate(instance=instance, schema=schema)
    except _JSError as e:
        return e.message
    except Exception:
        return None  # malformed schema: skip rather than crash
    return None


# Known stdlib modules (Python 3.12) — allow these without permission
_STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
    "json", "sys", "os", "pathlib", "re", "math", "datetime", "collections",
    "itertools", "functools", "typing", "string", "io", "abc", "copy",
    "logging", "time", "random", "struct", "textwrap", "unicodedata",
    "hashlib", "base64", "csv", "html", "urllib", "http", "socket",
    "ssl", "subprocess", "threading", "multiprocessing", "tempfile",
    "shutil", "glob", "fnmatch", "stat", "fileinput", "difflib",
    "pprint", "traceback", "warnings", "contextlib", "dataclasses",
    "enum", "numbers", "operator", "decimal", "fractions",
    "ast", "dis", "inspect", "importlib", "ctypes",
}

_NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib.request", "urllib.parse", "requests", "httplib"}
_SUBPROCESS_MODULES = {"subprocess", "ctypes"}


def _validate_manifest(manifest: dict, registry: dict) -> dict:
    """Stage 1: Manifest lint. Returns {"ok": bool, "details": [...]}. """
    details = []
    tool_name = manifest.get("name", "")
    version = manifest.get("version", "")
    description = manifest.get("description", "")

    if not tool_name or not re.match(r"^[a-z][a-z0-9_]*$", tool_name):
        details.append(f"Invalid tool name: {tool_name}")
    if not _SEMVER_RE.match(version):
        details.append(f"Invalid version: {version}")
    if len(description) < 10:
        details.append("Description must be at least 10 characters")
    is_workflow = manifest.get("kind") == "workflow"
    if not is_workflow:
        if "input_schema" not in manifest:
            details.append("Missing input_schema")
        if "output_schema" not in manifest:
            details.append("Missing output_schema")
        # Validate that schemas are proper JSON Schema
        for key in ("input_schema", "output_schema"):
            schema = manifest.get(key)
            if schema is None:
                continue
            if Draft7Validator is not None:
                try:
                    Draft7Validator.check_schema(schema)
                except SchemaError as e:
                    details.append(f"{key} is not a valid JSON Schema: {e.message}")
                    continue
            if "type" not in schema:
                details.append(f'{key} needs a "type" (input_schema: "object" with "properties")')
        # Require input_schema to be {"type": "object", "properties": {...}, "required": [...]}
        ins = manifest.get("input_schema")
        if isinstance(ins, dict) and not (ins.get("type") == "object" and isinstance(ins.get("properties"), dict)):
            details.append('input_schema must look like {"type": "object", "properties": {...}, "required": [...]}')
    examples = manifest.get("examples", [])
    if not is_workflow:
        if not examples:
            details.append("At least 1 example required")
        elif len(examples) > 20:
            details.append("At most 20 examples allowed")
        else:
            for i, ex in enumerate(examples):
                if "input" not in ex:
                    details.append(f"Example {i}: missing 'input'")
                if "output" not in ex:
                    details.append(f"Example {i}: missing 'output'")
                # Validate examples against schemas
                if "input" in ex:
                    err = _schema_error(ex["input"], manifest.get("input_schema", {}))
                    if err:
                        details.append(f"Example {i} input violates input_schema: {err}")
                if "output" in ex:
                    err = _schema_error(ex["output"], manifest.get("output_schema", {}))
                    if err:
                        details.append(f"Example {i} output violates output_schema: {err}")

    if "permissions" not in manifest:
        manifest["permissions"] = {}

    # Version ordering
    if tool_name and _SEMVER_RE.match(version) and tool_name in registry:
        existing_versions = list(registry[tool_name].get("versions", {}).keys())
        if existing_versions:
            latest = sorted(existing_versions, key=lambda v: _semver_parse(v), reverse=True)[0]
            if _semver_compare(version, latest) <= 0:
                details.append(f"Version {version} must be greater than latest {latest}")
            if version in registry[tool_name].get("versions", {}):
                details.append(f"Version {version} already exists")

    # Workflow validation
    if is_workflow:
        steps = manifest.get("steps", [])
        if not steps:
            details.append("Workflow must have at least 1 step")
        step_ids = set()
        for s in steps:
            sid = s.get("id", "")
            if sid in step_ids:
                details.append(f"Duplicate step id: {sid}")
            step_ids.add(sid)
            if s.get("tool") == tool_name:
                details.append(f"Step '{sid}' references itself")

    return {"ok": len(details) == 0, "details": details}


def _static_check(code: str, manifest: dict) -> dict:
    """Stage 2: Static checks with tree-sitter. Returns {"ok": bool, "details": [...]}. """
    details = []
    permissions = manifest.get("permissions", {})

    if not HAS_TREE_SITTER:
        details.append("tree-sitter not installed; static check skipped")
        return {"ok": True, "details": details}

    tree = _TS_PARSER.parse(bytes(code, "utf-8"))
    if tree.root_node.has_error:
        # Tree-sitter recovers from errors, but note them
        pass

    # Collect all imports and calls by walking the AST
    imports = []  # list of module names (top-level)
    calls = []    # list of function names

    def _walk(node):
        for child in node.children:
            if child.type == "import_statement":
                # import socket / import sys, json
                for sub in child.children:
                    if sub.type == "dotted_name":
                        imports.append(sub.text.decode("utf-8"))
            elif child.type == "import_from_statement":
                # from os import path — first dotted_name is the module
                found_import_kw = False
                for sub in child.children:
                    if sub.type == "import":
                        found_import_kw = True
                        continue
                    if sub.type == "dotted_name":
                        if not found_import_kw:
                            # This is the "from" module (e.g., "os")
                            imports.append(sub.text.decode("utf-8"))
            elif child.type == "call":
                for sub in child.children:
                    if sub.type == "identifier":
                        calls.append(sub.text.decode("utf-8"))
                    elif sub.type == "attribute":
                        # e.g., os.system() — get the attribute name
                        for attr_child in sub.children:
                            if attr_child.type == "identifier":
                                calls.append(attr_child.text.decode("utf-8"))
            _walk(child)

    _walk(tree.root_node)

    # Check imports against permissions
    for mod_full in imports:
        mod = mod_full.split(".")[0]
        if mod in _NETWORK_MODULES and not permissions.get("network"):
            details.append(f"Import '{mod_full}' requires permissions.network")
        if mod in _SUBPROCESS_MODULES and not permissions.get("subprocess"):
            details.append(f"Import '{mod_full}' requires permissions.subprocess")
        if mod_full in ("__import__", "importlib"):
            details.append(f"Import '{mod_full}' is not allowed")

    # Check calls
    for call_name in calls:
        if call_name == "__import__":
            details.append("Call to __import__ is not allowed")

    return {"ok": len(details) == 0, "details": details}


def _run_example_tests(code: str, manifest: dict) -> dict:
    """Stage 3: Run manifest examples in sandbox. Returns {"ok": bool, "details": [...]}. """
    details = []
    examples = manifest.get("examples", [])
    if not examples:
        return {"ok": True, "details": ["No examples to test"]}

    is_workflow = manifest.get("kind") == "workflow"
    if is_workflow:
        return {"ok": True, "details": ["Workflow tools skip example tests"]}

    # Write code to temp file
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    try:
        script = tmp_dir / "main.py"
        script.write_text(code, encoding="utf-8")
        workspace = WORKSPACE_DIR
        workspace.mkdir(parents=True, exist_ok=True)

        for i, ex in enumerate(examples):
            inp = ex.get("input", {})
            expected = ex.get("output", {})
            result = run_tool(str(script), inp, timeout=5.0, cwd=str(workspace))
            if not result["ok"]:
                details.append(f"Example {i}: tool error — {result.get('error', 'unknown')}: {result.get('detail', '')}")
                continue
            actual = result["output"]
            if actual != expected:
                details.append(f"Example {i}: expected {expected}, got {actual}")

        # Determinism check: run example 0 twice
        if examples:
            r1 = run_tool(str(script), examples[0].get("input", {}), timeout=5.0, cwd=str(workspace))
            r2 = run_tool(str(script), examples[0].get("input", {}), timeout=5.0, cwd=str(workspace))
            if r1["ok"] and r2["ok"] and r1["output"] != r2["output"]:
                details.append("Example 0 is non-deterministic: two runs gave different output")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"ok": len(details) == 0, "details": details}


def _run_regression_tests(tool_name: str, version: str, code: str, manifest: dict, registry: dict) -> dict:
    """Stage 4: Regression tests. Returns {"ok": bool, "details": [...]}. """
    details = []
    if tool_name not in registry:
        return {"ok": True, "details": ["New tool — no regression tests"]}

    new_major = _semver_parse(version)[0]
    entry = registry[tool_name]
    is_workflow = manifest.get("kind") == "workflow"

    # Check earlier versions with same MAJOR
    for ver, info in entry.get("versions", {}).items():
        if ver == version:
            continue
        ver_major = _semver_parse(ver)[0]
        if ver_major != new_major:
            continue
        # Load old manifest and run its examples with new code
        old_manifest_path = TOOLS_DIR / tool_name / ver / "manifest.json"
        if not old_manifest_path.is_file():
            continue
        old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        old_examples = old_manifest.get("examples", [])
        if not old_examples or is_workflow:
            continue

        tmp_dir = pathlib.Path(tempfile.mkdtemp())
        try:
            script = tmp_dir / "main.py"
            script.write_text(code, encoding="utf-8")
            workspace = WORKSPACE_DIR
            for i, ex in enumerate(old_examples):
                inp = ex.get("input", {})
                expected = ex.get("output", {})
                result = run_tool(str(script), inp, timeout=5.0, cwd=str(workspace))
                if not result["ok"]:
                    details.append(f"Regression v{ver} example {i}: tool error")
                    continue
                if result["output"] != expected:
                    details.append(f"Regression v{ver} example {i}: output changed (breaking change requires MAJOR bump)")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"ok": len(details) == 0, "details": details}


def _check_permission_escalation(tool_name: str, new_permissions: dict, registry: dict) -> dict:
    """Stage 5: Permission escalation check. Returns {"ok": bool, "details": [...]}. """
    details = []
    if tool_name not in registry:
        return {"ok": True, "details": ["New tool — no escalation check"]}

    entry = registry[tool_name]
    active = entry.get("active")
    if not active:
        return {"ok": True, "details": ["No active version"]}

    old_permissions = entry.get("versions", {}).get(active, {}).get("permissions", {})
    new_keys = set(new_permissions.keys())
    old_keys = set(old_permissions.keys())
    escalated = new_keys - old_keys
    if escalated:
        details.append(f"New permissions {escalated} require approval")
        return {"ok": False, "details": details, "needs_approval": True}

    return {"ok": True, "details": ["No permission escalation"]}


def _promote_version(tool_name: str, version: str, code: str, manifest: dict, registry: dict) -> dict:
    """Stage 6: Promote — install as immutable version. Returns {"ok": bool, "details": [...]}. """
    details = []
    is_workflow = manifest.get("kind") == "workflow"

    version_dir = TOOLS_DIR / tool_name / version
    version_dir.mkdir(parents=True, exist_ok=True)

    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if not is_workflow and code:
        (version_dir / "main.py").write_text(code, encoding="utf-8")

    if tool_name not in registry:
        registry[tool_name] = _default_registry_entry()
    entry = registry[tool_name]
    entry["active"] = version
    entry["versions"][version] = {
        "description": manifest.get("description", ""),
        "tags": manifest.get("tags", []),
        "permissions": manifest.get("permissions", {}),
        "healthy": True,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    entry["quarantined"] = False

    # Warn on similar descriptions
    for other_name, other_entry in registry.items():
        if other_name == tool_name:
            continue
        other_active = other_entry.get("active")
        if other_active:
            other_desc = other_entry.get("versions", {}).get(other_active, {}).get("description", "")
            my_desc = manifest.get("description", "")
            if other_desc and my_desc and other_desc.lower() == my_desc.lower():
                details.append(f"Warning: description identical to '{other_name}'")

    return {"ok": True, "details": [f"Installed {tool_name} v{version}"] + details}


def _find_similar_tools(description: str, registry: dict) -> list:
    """Find tools with very similar descriptions."""
    similar = []
    desc_lower = description.lower()
    for name, entry in registry.items():
        active = entry.get("active")
        if active:
            other_desc = entry.get("versions", {}).get(active, {}).get("description", "")
            if other_desc and other_desc.lower() == desc_lower:
                similar.append(name)
    return similar


# ---------------------------------------------------------------------------
# Submit tool — full 6-stage validation pipeline
# ---------------------------------------------------------------------------

def submit_tool(manifest_data: dict, code: str = "", dry_run: bool = False) -> dict:
    """Submit a new or updated tool through the 6-stage validation pipeline.
    Returns structured report: {"ok", "status", "stages": [{"name", "ok", "details"}]}.
    Every run is logged to logs/validation.json for observability."""
    registry = load_registry()
    stages = []

    tool_name = manifest_data.get("name", "")
    version = manifest_data.get("version", "1.0.0")
    is_workflow = manifest_data.get("kind") == "workflow"

    def _result(ok, status):
        """Build result dict and log it."""
        result = {"ok": ok, "status": status, "stages": stages}
        _log_validation({
            "event": "submit_tool",
            "tool": tool_name,
            "version": version,
            "status": status,
            "dry_run": dry_run,
            "stages": stages,
        })
        return result

    # Stage 1: Manifest lint
    stage = _validate_manifest(manifest_data, registry)
    stages.append({"name": "manifest_lint", "ok": stage["ok"], "details": stage["details"]})
    if not stage["ok"]:
        return _result(False, "rejected")

    # Stage 2: Static checks (skip for workflows)
    if not is_workflow and code:
        stage = _static_check(code, manifest_data)
        stages.append({"name": "static_check", "ok": stage["ok"], "details": stage["details"]})
        if not stage["ok"]:
            return _result(False, "rejected")

    # Stage 3: Example tests (skip for workflows)
    if not is_workflow and code:
        stage = _run_example_tests(code, manifest_data)
        stages.append({"name": "example_tests", "ok": stage["ok"], "details": stage["details"]})
        if not stage["ok"]:
            return _result(False, "rejected")

    # Stage 4: Regression tests (skip for workflows)
    if not is_workflow and code:
        stage = _run_regression_tests(tool_name, version, code, manifest_data, registry)
        stages.append({"name": "regression_tests", "ok": stage["ok"], "details": stage["details"]})
        if not stage["ok"]:
            return _result(False, "rejected")

    # Stage 5: Permission escalation
    stage = _check_permission_escalation(tool_name, manifest_data.get("permissions", {}), registry)
    stages.append({"name": "permission_escalation", "ok": stage["ok"], "details": stage["details"]})
    if not stage["ok"]:
        if stage.get("needs_approval"):
            return _result(False, "needs_approval")
        return _result(False, "rejected")

    if dry_run:
        stages.append({"name": "dry_run", "ok": True, "details": ["Validation passed (dry run)"]})
        return _result(True, "accepted")

    # Stage 6: Promote
    stage = _promote_version(tool_name, version, code, manifest_data, registry)
    stages.append({"name": "promote", "ok": stage["ok"], "details": stage["details"]})
    if not stage["ok"]:
        return _result(False, "rejected")

    save_registry(registry)
    return _result(True, "accepted")


# ---------------------------------------------------------------------------
# Execute — unified dispatcher (never raises)
# ---------------------------------------------------------------------------

def execute(name: str, args: dict) -> dict:
    """Unified dispatcher for all operations. Never raises — always returns a dict."""
    try:
        if name == "ws_write":
            return _ws_write(args.get("path", ""), args.get("content", ""))
        elif name == "ws_list":
            return _ws_list(args.get("path", ""))
        elif name == "search_capabilities":
            results = search_capabilities(args.get("query", ""), args.get("limit", 5))
            return {"result": results}
        elif name == "submit_tool":
            manifest = args.get("manifest", {})
            code = args.get("code", "")
            dry_run = args.get("dry_run", False)
            return submit_tool(manifest, code, dry_run=dry_run)
        elif name == "rollback_tool":
            return _rollback_tool(args.get("name", ""), args.get("version"))
        elif name == "run_code":
            return _run_code(args.get("code", ""), args.get("timeout", 10.0))
        elif name == "list_available_tools":
            return {"result": list_available_tools()}
        else:
            # Try as a regular tool
            return call_tool(name, args)
    except Exception as e:
        # Absolute last resort — never raise to the agent
        return {"error": "harness_error", "detail": f"{type(e).__name__}: {e}"}
