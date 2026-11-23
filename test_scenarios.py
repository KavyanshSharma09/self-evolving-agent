"""
Integration tests for the 4 scenarios described in the issue:
1. Reuse — search and call existing word_frequency tool
2. Adapt — update word_frequency with optional top_n argument (v1.1.0)
3. Fallback — crash v1.1.0, verify fallback to v1.0.0
4. Compose — create a workflow combining word_frequency + word_count

Run with: pytest test_scenarios.py -v
"""

import json
import os
import pathlib
import shutil
import tempfile
import pytest

# Use a fresh tools/workspace directory per test
@pytest.fixture(scope="function")
def fresh_env(tmp_path):
    """Set up a fresh tools/ and workspace/ directory for each test."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    # Patch harness paths
    import harness
    original_tools = harness.TOOLS_DIR
    original_workspace = harness.WORKSPACE_DIR
    original_registry = harness.REGISTRY_PATH

    harness.TOOLS_DIR = tools_dir
    harness.WORKSPACE_DIR = workspace_dir
    harness.REGISTRY_PATH = tools_dir / "registry.json"

    yield workspace_dir, tools_dir

    # Restore
    harness.TOOLS_DIR = original_tools
    harness.WORKSPACE_DIR = original_workspace
    harness.REGISTRY_PATH = original_registry


def test_reuse_scenario(fresh_env):
    """Test 1: Reuse — Create word_frequency, then search and call it."""
    workspace_dir, tools_dir = fresh_env
    import harness

    # Step A: Create the word_frequency tool
    test_file = workspace_dir / "test.txt"
    test_file.write_text("go go go stop stop wait\n", encoding="utf-8")

    code = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:3]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest = {
        "name": "word_frequency",
        "version": "1.0.0",
        "description": "Counts word frequencies in a file and returns the 3 most common words.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {
            "type": "object",
            "properties": {}
        },
        "examples": [
            {
                "input": {"path": "test.txt"},
                "output": {"go": 3, "stop": 2, "wait": 1}
            }
        ],
        "permissions": {},
        "tags": ["text", "frequency", "words"]
    }

    # Submit the tool
    result = harness.submit_tool(manifest, code)
    assert result["ok"], f"Submit failed: {result}"
    assert result["status"] == "accepted"

    # Step B: Search for it
    search = harness.search_capabilities("word frequency")
    assert len(search) >= 1
    assert search[0]["name"] == "word_frequency"

    # Step C: Call it
    call_result = harness.call_tool("word_frequency", {"path": "test.txt"})
    assert "error" not in call_result, f"Call failed: {call_result}"
    result_data = call_result["result"]
    assert result_data["go"] == 3
    assert result_data["stop"] == 2
    assert result_data["wait"] == 1


def test_adapt_scenario(fresh_env):
    """Test 2: Adapt — Update word_frequency with optional top_n (v1.1.0)."""
    workspace_dir, tools_dir = fresh_env
    import harness

    # Create test file
    test_file = workspace_dir / "test.txt"
    test_file.write_text("go go go stop stop wait\n", encoding="utf-8")

    # First, create v1.0.0
    code_v1 = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:3]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_v1 = {
        "name": "word_frequency",
        "version": "1.0.0",
        "description": "Counts word frequencies in a file and returns the 3 most common words.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {
            "type": "object",
            "properties": {}
        },
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}}
        ],
        "permissions": {},
        "tags": ["text", "frequency", "words"]
    }

    result = harness.submit_tool(manifest_v1, code_v1)
    assert result["ok"], f"v1.0.0 submit failed: {result}"

    # Now adapt to v1.1.0 with optional top_n
    code_v11 = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
top_n = data.get("top_n", 3)
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:top_n]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_v11 = {
        "name": "word_frequency",
        "version": "1.1.0",
        "description": "Counts word frequencies in a file and returns the top N most common words (default 3).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "top_n": {"type": "integer", "default": 3}
            },
            "required": ["path"]
        },
        "output_schema": {
            "type": "object",
            "properties": {}
        },
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}},
            {"input": {"path": "test.txt", "top_n": 2}, "output": {"go": 3, "stop": 2}}
        ],
        "permissions": {},
        "tags": ["text", "frequency", "words"]
    }

    result = harness.submit_tool(manifest_v11, code_v11)
    assert result["ok"], f"v1.1.0 adapt failed: {result}"
    assert result["status"] == "accepted"

    # Verify v1.1.0 is now active
    registry = harness.load_registry()
    assert registry["word_frequency"]["active"] == "1.1.0"

    # Verify old v1.0.0 example still passes (regression)
    call_result = harness.call_tool("word_frequency", {"path": "test.txt"})
    assert "error" not in call_result
    assert call_result["result"]["go"] == 3

    # Verify new top_n works
    call_result2 = harness.call_tool("word_frequency", {"path": "test.txt", "top_n": 2})
    assert "error" not in call_result2
    result_data = call_result2["result"]
    assert len(result_data) == 2
    assert result_data["go"] == 3
    assert result_data["stop"] == 2


def test_adapt_breaking_change_rejected(fresh_env):
    """Test: Breaking change without MAJOR bump is rejected."""
    workspace_dir, tools_dir = fresh_env
    import harness

    test_file = workspace_dir / "test.txt"
    test_file.write_text("go go go stop stop wait\n", encoding="utf-8")

    # Create v1.0.0
    code_v1 = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:3]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_v1 = {
        "name": "word_frequency",
        "version": "1.0.0",
        "description": "Counts word frequencies in a file and returns the 3 most common words.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {"type": "object", "properties": {}},
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}}
        ],
        "permissions": {},
        "tags": ["text", "frequency"]
    }

    result = harness.submit_tool(manifest_v1, code_v1)
    assert result["ok"]

    # Try v1.1.0 that changes output format (breaking change)
    code_breaking = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:3]
# Changed output format: list instead of dict
result = [{"word": item[0], "count": item[1]} for item in top]
print(json.dumps(result))
'''

    manifest_breaking = {
        "name": "word_frequency",
        "version": "1.1.0",
        "description": "Counts word frequencies — returns list of word/count pairs.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {"type": "array", "items": {"type": "object"}},
        "examples": [
            {"input": {"path": "test.txt"},
             "output": [{"word": "go", "count": 3}, {"word": "stop", "count": 2}, {"word": "wait", "count": 1}]}
        ],
        "permissions": {},
        "tags": ["text", "frequency"]
    }

    result = harness.submit_tool(manifest_breaking, code_breaking)
    # Should be rejected because v1.0.0 example no longer passes
    assert not result["ok"], f"Breaking change should be rejected: {result}"
    # Check regression stage failed
    regression_stage = [s for s in result["stages"] if s["name"] == "regression_tests"]
    assert len(regression_stage) >= 1
    assert not regression_stage[0]["ok"]


def test_fallback_scenario(fresh_env):
    """Test 3: Fallback — Crash v1.1.0, verify fallback to v1.0.0."""
    workspace_dir, tools_dir = fresh_env
    import harness

    test_file = workspace_dir / "test.txt"
    test_file.write_text("go go go stop stop wait\n", encoding="utf-8")

    # Create v1.0.0
    code_v1 = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:3]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_v1 = {
        "name": "word_frequency",
        "version": "1.0.0",
        "description": "Counts word frequencies in a file and returns the 3 most common words.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {"type": "object", "properties": {}},
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}}
        ],
        "permissions": {},
        "tags": ["text", "frequency"]
    }

    harness.submit_tool(manifest_v1, code_v1)

    # Create v1.1.0 (healthy)
    code_v11 = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
top_n = data.get("top_n", 3)
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:top_n]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_v11 = {
        "name": "word_frequency",
        "version": "1.1.0",
        "description": "Counts word frequencies with optional top_n.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "top_n": {"type": "integer"}
            },
            "required": ["path"]
        },
        "output_schema": {"type": "object", "properties": {}},
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}}
        ],
        "permissions": {},
        "tags": ["text", "frequency"]
    }

    harness.submit_tool(manifest_v11, code_v11)

    # Now manually corrupt v1.1.0's main.py to crash
    v11_main = tools_dir / "word_frequency" / "1.1.0" / "main.py"
    v11_main.write_text('raise RuntimeError("boom")\n', encoding="utf-8")

    # Call should fall back to v1.0.0
    call_result = harness.call_tool("word_frequency", {"path": "test.txt"})
    assert "error" not in call_result, f"Fallback failed: {call_result}"
    assert "note" in call_result, "Fallback should include a note"
    assert call_result["result"]["go"] == 3

    # Registry should show v1.1.0 as unhealthy
    registry = harness.load_registry()
    assert not registry["word_frequency"]["versions"]["1.1.0"]["healthy"]


def test_compose_scenario(fresh_env):
    """Test 4: Compose — Create workflow combining word_frequency + word_count."""
    workspace_dir, tools_dir = fresh_env
    import harness

    test_file = workspace_dir / "test.txt"
    test_file.write_text("go go go stop stop wait\n", encoding="utf-8")

    # Create word_frequency tool
    code_wf = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
top_n = data.get("top_n", 3)
with open(path, "r") as f:
    text = f.read()
words = text.lower().split()
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
sorted_words = sorted(counts.items(), key=lambda x: -x[1])
top = sorted_words[:top_n]
result = {item[0]: item[1] for item in top}
print(json.dumps(result))
'''

    manifest_wf = {
        "name": "word_frequency",
        "version": "1.0.0",
        "description": "Counts word frequencies in a file and returns the top N most common words.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "top_n": {"type": "integer"}
            },
            "required": ["path"]
        },
        "output_schema": {"type": "object", "properties": {}},
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"go": 3, "stop": 2, "wait": 1}}
        ],
        "permissions": {},
        "tags": ["text", "frequency"]
    }

    harness.submit_tool(manifest_wf, code_wf)

    # Create word_count tool
    code_wc = '''import json, sys
data = json.load(sys.stdin)
path = data["path"]
with open(path, "r") as f:
    text = f.read()
words = text.split()
print(json.dumps({"total": len(words)}))
'''

    manifest_wc = {
        "name": "word_count",
        "version": "1.0.0",
        "description": "Counts the total number of words in a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {
            "type": "object",
            "properties": {"total": {"type": "integer"}}
        },
        "examples": [
            {"input": {"path": "test.txt"}, "output": {"total": 6}}
        ],
        "permissions": {},
        "tags": ["text", "count"]
    }

    harness.submit_tool(manifest_wc, code_wc)

    # Create a workflow that combines both
    workflow_manifest = {
        "name": "file_analysis",
        "version": "1.0.0",
        "description": "Analyzes a file: returns the most common word and total word count.",
        "kind": "workflow",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "output_schema": {"type": "object", "properties": {}},
        "steps": [
            {
                "id": "freq",
                "tool": "word_frequency",
                "input": {"path": "$input.path", "top_n": 1}
            },
            {
                "id": "count",
                "tool": "word_count",
                "input": {"path": "$input.path"}
            }
        ],
        "output": {
            "most_common": "$steps.freq",
            "total_count": "$steps.count.total"
        },
        "examples": [
            {
                "input": {"path": "test.txt"},
                "output": {"most_common": {"go": 3}, "total_count": 6}
            }
        ],
        "permissions": {},
        "tags": ["text", "analysis", "workflow"]
    }

    result = harness.submit_tool(workflow_manifest, code="")
    assert result["ok"], f"Workflow submit failed: {result}"

    # Call the workflow
    call_result = harness.call_tool("file_analysis", {"path": "test.txt"})
    assert "error" not in call_result, f"Workflow call failed: {call_result}"
    result_data = call_result["result"]
    assert result_data["most_common"]["go"] == 3
    assert result_data["total_count"] == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
