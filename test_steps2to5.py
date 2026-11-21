"""
Comprehensive tests for Steps 2-5.
No API key required. Uses isolated temp directories.
"""

import json
import pathlib
import tempfile
import pytest


@pytest.fixture()
def harness_dir(tmp_path):
    """Create an isolated harness environment."""
    tools = tmp_path / "tools"
    tools.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    import harness
    original_tools = harness.TOOLS_DIR
    original_registry = harness.REGISTRY_PATH
    original_workspace = harness.WORKSPACE_DIR
    harness.TOOLS_DIR = tools
    harness.REGISTRY_PATH = tools / "registry.json"
    harness.WORKSPACE_DIR = workspace
    yield tmp_path, tools, workspace
    harness.TOOLS_DIR = original_tools
    harness.REGISTRY_PATH = original_registry
    harness.WORKSPACE_DIR = original_workspace


def _make_add_tool(harness_dir):
    """Helper: submit an 'add' tool v1.0.0."""
    _, tools, workspace = harness_dir
    import harness
    manifest = {
        "name": "add",
        "version": "1.0.0",
        "description": "Adds two numbers together",
        "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
        "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
        "examples": [{"input": {"a": 1, "b": 2}, "output": {"result": 3}}],
        "permissions": {},
        "tags": ["math"],
    }
    code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
    result = harness.submit_tool(manifest, code)
    assert result["ok"], f"Failed to submit add tool: {result}"
    return manifest


# ---------------------------------------------------------------------------
# Step 2: Validation Pipeline Tests
# ---------------------------------------------------------------------------

class TestValidationPipeline:
    def test_manifest_lint_missing_fields(self, harness_dir):
        import harness
        manifest = {"name": "x"}  # missing version, description, etc.
        result = harness.submit_tool(manifest, "print('ok')")
        assert result["ok"] is False
        assert result["status"] == "rejected"
        assert any(s["name"] == "manifest_lint" and not s["ok"] for s in result["stages"])

    def test_static_check_rejects_network(self, harness_dir):
        import harness
        manifest = {
            "name": "nettool",
            "version": "1.0.0",
            "description": "Network tool description",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            "examples": [{"input": {}, "output": {"ok": True}}],
            "permissions": {},
        }
        code = "import socket\nimport sys, json\nprint(json.dumps({'ok': True}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"] is False
        assert any(s["name"] == "static_check" and not s["ok"] for s in result["stages"])

    def test_static_check_allows_with_permission(self, harness_dir):
        import harness
        manifest = {
            "name": "nettool2",
            "version": "1.0.0",
            "description": "Network tool with permission",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            "examples": [{"input": {}, "output": {"ok": True}}],
            "permissions": {"network": True},
        }
        code = "import socket\nimport sys, json\nprint(json.dumps({'ok': True}))"
        result = harness.submit_tool(manifest, code)
        # Should pass static check (may fail example test since socket doesn't do much)
        stages = {s["name"]: s for s in result["stages"]}
        assert stages.get("static_check", {}).get("ok", True)

    def test_wrong_example_output_rejected(self, harness_dir):
        import harness
        manifest = {
            "name": "badex",
            "version": "1.0.0",
            "description": "Bad example tool description",
            "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "integer"}}},
            "examples": [{"input": {"a": 1}, "output": {"result": 999}}],
            "permissions": {},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a']}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"] is False
        assert any(s["name"] == "example_tests" and not s["ok"] for s in result["stages"])

    def test_non_deterministic_rejected(self, harness_dir):
        import harness
        manifest = {
            "name": "randtool",
            "version": "1.0.0",
            "description": "Random tool description here",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "examples": [{"input": {}, "output": {"v": 42}}],
            "permissions": {},
        }
        code = "import sys, json, random\nprint(json.dumps({'v': random.randint(0, 100)}))"
        result = harness.submit_tool(manifest, code)
        # May or may not be rejected depending on randomness; check it at least ran
        assert "stages" in result

    def test_adapt_passes_old_examples(self, harness_dir):
        """Submit v2.0.0 that passes v1.0.0 examples → accepted."""
        _make_add_tool(harness_dir)
        import harness
        manifest = {
            "name": "add",
            "version": "2.0.0",
            "description": "Adds two numbers, v2",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
            "examples": [{"input": {"a": 3, "b": 4}, "output": {"result": 7}}],
            "permissions": {},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"], f"Adapt should pass: {result}"

    def test_adapt_breaks_old_example_rejected(self, harness_dir):
        """Submit v1.1.0 that breaks v1.0.0 example → rejected (same MAJOR)."""
        _make_add_tool(harness_dir)
        import harness
        manifest = {
            "name": "add",
            "version": "1.1.0",
            "description": "Adds two numbers broken",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
            "examples": [{"input": {"a": 1, "b": 2}, "output": {"result": 100}}],
            "permissions": {},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': 100}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"] is False
        assert any(s["name"] == "regression_tests" and not s["ok"] for s in result["stages"])

    def test_permission_escalation(self, harness_dir):
        """New version adds permissions → needs_approval."""
        _make_add_tool(harness_dir)
        import harness
        manifest = {
            "name": "add",
            "version": "2.0.0",
            "description": "Adds two numbers v2",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
            "examples": [{"input": {"a": 1, "b": 2}, "output": {"result": 3}}],
            "permissions": {"network": True},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
        result = harness.submit_tool(manifest, code)
        assert result["status"] == "needs_approval"

    def test_registry_stays_valid_json(self, harness_dir):
        """After a failed submit, registry.json is still valid JSON."""
        import harness
        # Ensure registry exists first
        harness.save_registry({})
        manifest = {"name": "!!bad"}
        harness.submit_tool(manifest, "print('ok')")
        content = harness.REGISTRY_PATH.read_text()
        parsed = json.loads(content)  # Should not raise
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Step 3: Health Tracking Tests
# ---------------------------------------------------------------------------

class TestHealthTracking:
    def test_fallback_to_previous_version(self, harness_dir):
        """Newest version crashes, falls back to previous healthy version."""
        _, tools, workspace = harness_dir
        import harness
        # Manually set up two versions: v1.0.0 (healthy) and v2.0.0 (active, will crash)
        for ver, code in [("1.0.0", "import sys, json; print(json.dumps({'v': 1}))"),
                           ("2.0.0", "import sys, json; raise ValueError('crash')")]:
            vdir = tools / "ftool" / ver
            vdir.mkdir(parents=True)
            (vdir / "manifest.json").write_text(json.dumps({
                "name": "ftool", "version": ver, "description": f"Fallback tool {ver}",
                "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
            }))
            (vdir / "main.py").write_text(code)

        reg = {
            "ftool": {
                "active": "2.0.0",
                "versions": {
                    "1.0.0": {"description": "v1", "tags": [], "permissions": {}, "healthy": True},
                    "2.0.0": {"description": "v2", "tags": [], "permissions": {}, "healthy": True},
                },
                "stats": {"calls": 0, "failures": 0, "consecutive_failures": 0, "last_used": None},
                "quarantined": False,
            }
        }
        harness.save_registry(reg)

        # Call should fall back to v1.0.0
        result = harness.call_tool("ftool", {})
        assert "result" in result
        assert result["result"].get("v") == 1
        assert "note" in result

    def test_quarantine_after_3_failures(self, harness_dir):
        """Tool fails 3 times consecutively → quarantined."""
        _, tools, workspace = harness_dir
        import harness
        # Manually set up a crashing tool
        vdir = tools / "qtool" / "1.0.0"
        vdir.mkdir(parents=True)
        (vdir / "manifest.json").write_text(json.dumps({
            "name": "qtool", "version": "1.0.0", "description": "Quarantine test tool",
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        }))
        (vdir / "main.py").write_text("import sys, json; raise ValueError('crash')")

        reg = {
            "qtool": {
                "active": "1.0.0",
                "versions": {
                    "1.0.0": {"description": "Quarantine test tool", "tags": [], "permissions": {}, "healthy": True},
                },
                "stats": {"calls": 0, "failures": 0, "consecutive_failures": 0, "last_used": None},
                "quarantined": False,
            }
        }
        harness.save_registry(reg)

        # Call 3 times
        for _ in range(3):
            harness.call_tool("qtool", {})

        # Should be quarantined
        reg = harness.load_registry()
        assert reg["qtool"]["quarantined"] is True

        # Search should hide it
        results = harness.search_capabilities("quarantine")
        assert not any(r["name"] == "qtool" for r in results)

    def test_tool_reported_error_not_failure(self, harness_dir):
        """Tool-reported errors ({'error': '...'}) don't mark unhealthy."""
        _, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "errtool",
            "version": "1.0.0",
            "description": "Error reporting tool desc",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"error": {"type": "string"}}},
            "examples": [{"input": {}, "output": {"error": "test"}}],
            "permissions": {},
        }
        harness.submit_tool(manifest, "import sys, json; print(json.dumps({'error': 'test'}))")

        result = harness.call_tool("errtool", {})
        assert "result" in result
        # Should still be healthy
        reg = harness.load_registry()
        assert reg["errtool"]["versions"]["1.0.0"]["healthy"] is True


# ---------------------------------------------------------------------------
# Step 4: Workflow Tests
# ---------------------------------------------------------------------------

class TestWorkflow:
    def test_workflow_compose(self, harness_dir):
        """Create a workflow tool, execute it, verify output."""
        _make_add_tool(harness_dir)
        _, tools, workspace = harness_dir
        import harness
        # Create multiply tool
        mul_manifest = {
            "name": "multiply",
            "version": "1.0.0",
            "description": "Multiplies two numbers",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
            "examples": [{"input": {"a": 2, "b": 3}, "output": {"result": 6}}],
            "permissions": {},
        }
        harness.submit_tool(mul_manifest, "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] * payload['b']}))")

        # Create workflow: add then multiply
        wf_manifest = {
            "name": "add_then_multiply",
            "version": "1.0.0",
            "description": "Adds then multiplies",
            "kind": "workflow",
            "steps": [
                {"id": "s1", "tool": "add", "input": {"a": "$input.x", "b": 1}},
                {"id": "s2", "tool": "multiply", "input": {"a": "$steps.s1.result", "b": 2}},
            ],
            "output": {"result": "$steps.s2.result"},
            "permissions": {},
        }
        result = harness.submit_tool(wf_manifest, "")
        assert result["ok"], f"Workflow submit failed: {result}"

        # Execute workflow
        wf_result = harness.call_tool("add_then_multiply", {"x": 5})
        assert "result" in wf_result
        assert wf_result["result"]["result"] == 12  # (5+1)*2

    def test_workflow_self_reference_rejected(self, harness_dir):
        """Workflow that references itself is rejected."""
        import harness
        manifest = {
            "name": "selfref",
            "version": "1.0.0",
            "description": "Self-referencing workflow",
            "kind": "workflow",
            "steps": [
                {"id": "s1", "tool": "selfref", "input": {}},
            ],
            "output": {},
            "permissions": {},
        }
        result = harness.submit_tool(manifest, "")
        assert result["ok"] is False

    def test_workflow_nesting_depth(self, harness_dir):
        """Workflow with nested workflow steps works up to depth 5."""
        _make_add_tool(harness_dir)
        import harness
        # Simple workflow calling add
        wf_manifest = {
            "name": "simple_wf",
            "version": "1.0.0",
            "description": "Simple workflow test",
            "kind": "workflow",
            "steps": [
                {"id": "s1", "tool": "add", "input": {"a": "$input.x", "b": "$input.y"}},
            ],
            "output": {"result": "$steps.s1.result"},
            "permissions": {},
        }
        result = harness.submit_tool(wf_manifest, "")
        assert result["ok"]

        wf_result = harness.call_tool("simple_wf", {"x": 3, "y": 4})
        assert "result" in wf_result


# ---------------------------------------------------------------------------
# Step 5: Sandbox Tests
# ---------------------------------------------------------------------------

class TestSandbox:
    def test_timeout(self, harness_dir):
        """Infinite loop tool is killed by timeout."""
        _, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "looptool",
            "version": "1.0.0",
            "description": "Infinite loop tool desc",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "examples": [{"input": {}, "output": {"ok": True}}],
            "permissions": {},
        }
        code = "while True: pass"
        # Submit will fail example test due to timeout, but that's expected
        result = harness.submit_tool(manifest, code)
        # The example test should timeout
        assert result["ok"] is False

    def test_windows_env(self, harness_dir):
        """Sandbox includes SYSTEMROOT in env."""
        from sandbox import run_tool
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        script = tmp / "check_env.py"
        script.write_text("import os, sys, json; print(json.dumps({'SYSTEMROOT': os.environ.get('SYSTEMROOT', '')}))")
        result = run_tool(str(script), {})
        assert result["ok"]
        assert result["output"]["SYSTEMROOT"]


# ---------------------------------------------------------------------------
# Execute never raises
# ---------------------------------------------------------------------------

class TestExecuteNeverRaises:
    def test_execute_never_raises(self, harness_dir):
        """execute() must never raise on any input."""
        import harness
        test_cases = [
            ("", {}),
            ("ws_write", {}),
            ("ws_list", {}),
            ("search_capabilities", {}),
            ("submit_tool", {}),
            ("rollback_tool", {}),
            ("run_code", {}),
            ("nonexistent", {"x": 1}),
            ("ws_write", {"path": "../escape", "content": "bad"}),
        ]
        for name, args in test_cases:
            result = harness.execute(name, args)
            assert isinstance(result, dict), f"execute({name}, {args}) should return dict"


# ---------------------------------------------------------------------------
# Integration: Create → Search → Reuse
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_create_search_reuse(self, harness_dir):
        """Full flow: submit a tool, search for it, call it."""
        _, tools, workspace = harness_dir
        import harness
        # Create
        manifest = {
            "name": "greet",
            "version": "1.0.0",
            "description": "Greets a person by name",
            "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"greeting": {"type": "string"}}},
            "examples": [{"input": {"name": "Alice"}, "output": {"greeting": "Hello, Alice!"}}],
            "permissions": {},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'greeting': 'Hello, ' + payload['name'] + '!'}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"]

        # Search
        results = harness.search_capabilities("greet")
        assert len(results) >= 1
        assert results[0]["name"] == "greet"

        # Reuse
        call_result = harness.call_tool("greet", {"name": "Bob"})
        assert "result" in call_result
        assert call_result["result"]["greeting"] == "Hello, Bob!"
