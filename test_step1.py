"""
Step 1 Tests — Versioned tool storage, registry, search_capabilities, execute() dispatcher.
No API key required. Uses isolated temp directories for each test.
"""

import json
import os
import pathlib
import shutil
import tempfile
import pytest


@pytest.fixture()
def harness_dir(tmp_path):
    """Create an isolated harness environment with tools/ and workspace/ dirs."""
    tools = tmp_path / "tools"
    tools.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Patch harness module to use this temp dir
    import harness
    original_tools = harness.TOOLS_DIR
    original_registry = harness.REGISTRY_PATH
    original_workspace = harness.WORKSPACE_DIR
    harness.TOOLS_DIR = tools
    harness.REGISTRY_PATH = tools / "registry.json"
    harness.WORKSPACE_DIR = workspace
    yield tmp_path, tools, workspace
    # Restore
    harness.TOOLS_DIR = original_tools
    harness.REGISTRY_PATH = original_registry
    harness.WORKSPACE_DIR = original_workspace


class TestSemver:
    def test_parse_valid(self, harness_dir):
        import harness
        assert harness._semver_parse("1.0.0") == (1, 0, 0)
        assert harness._semver_parse("2.3.4") == (2, 3, 4)

    def test_parse_invalid(self, harness_dir):
        import harness
        with pytest.raises(ValueError):
            harness._semver_parse("1.0")
        with pytest.raises(ValueError):
            harness._semver_parse("abc")

    def test_compare(self, harness_dir):
        import harness
        assert harness._semver_compare("1.0.0", "2.0.0") == -1
        assert harness._semver_compare("2.0.0", "1.0.0") == 1
        assert harness._semver_compare("1.0.0", "1.0.0") == 0


class TestRegistry:
    def test_load_empty(self, harness_dir):
        import harness
        reg = harness.load_registry()
        assert reg == {}

    def test_save_and_load(self, harness_dir):
        import harness
        reg = {"mytool": {"active": "1.0.0", "versions": {}, "stats": {}, "quarantined": False}}
        harness.save_registry(reg)
        loaded = harness.load_registry()
        assert loaded == reg

    def test_atomic_write(self, harness_dir):
        """Registry file should never be partial JSON."""
        import harness
        reg = {"tool1": {"active": "1.0.0", "versions": {}, "stats": {}, "quarantined": False}}
        harness.save_registry(reg)
        # File should be valid JSON
        content = harness.REGISTRY_PATH.read_text()
        parsed = json.loads(content)
        assert parsed == reg


class TestMigration:
    def test_migrate_flat_tool(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Create a flat tool
        tool_dir = tools / "mytool"
        tool_dir.mkdir()
        (tool_dir / "manifest.json").write_text(json.dumps({
            "name": "mytool",
            "description": "A test tool for migration",
            "args": {"x": "number"},
        }))
        (tool_dir / "main.py").write_text("import sys, json; print(json.dumps({'result': 1}))")

        reg = harness.load_registry()
        reg = harness._migrate_flat_tools(reg)
        harness.save_registry(reg)

        # Check versioned structure
        version_dir = tools / "mytool" / "1.0.0"
        assert version_dir.is_dir()
        assert (version_dir / "manifest.json").is_file()
        assert (version_dir / "main.py").is_file()

        # Check registry
        assert "mytool" in reg
        assert reg["mytool"]["active"] == "1.0.0"
        assert "1.0.0" in reg["mytool"]["versions"]

    def test_skip_already_versioned(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Create a versioned tool
        version_dir = tools / "vtool" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "vtool",
            "version": "1.0.0",
            "description": "Already versioned tool",
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {"vtool": {"active": "1.0.0", "versions": {"1.0.0": {}}, "stats": {}, "quarantined": False}}
        harness.save_registry(reg)
        reg = harness._migrate_flat_tools(reg)

        # Should not change
        assert reg["vtool"]["active"] == "1.0.0"

    def test_skip_invalid_manifest(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        tool_dir = tools / "badtool"
        tool_dir.mkdir()
        (tool_dir / "manifest.json").write_text("not json")
        (tool_dir / "main.py").write_text("print('ok')")

        reg = harness._migrate_flat_tools({})
        assert "badtool" not in reg


class TestScanVersioned:
    def test_scan_empty(self, harness_dir):
        import harness
        tools = harness._scan_versioned({})
        assert tools == {}

    def test_scan_returns_active_tools(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Create a versioned tool
        version_dir = tools / "add" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "add",
            "version": "1.0.0",
            "description": "Adds two numbers",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }))
        (version_dir / "main.py").write_text("import sys, json; print(json.dumps({'result': 1}))")

        reg = {
            "add": {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": "Adds two numbers", "tags": [], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": False,
            }
        }
        scanned = harness._scan_versioned(reg)
        assert "add" in scanned
        assert scanned["add"]["_version"] == "1.0.0"
        assert scanned["add"]["_entry"].endswith("main.py")

    def test_scan_hides_quarantined(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        version_dir = tools / "bad" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "bad",
            "version": "1.0.0",
            "description": "Bad tool",
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "bad": {
                "active": "1.0.0",
                "versions": {"1.0.0": {}},
                "stats": {},
                "quarantined": True,
            }
        }
        scanned = harness._scan_versioned(reg)
        assert "bad" not in scanned


class TestSearchCapabilities:
    def test_search_by_name(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        version_dir = tools / "add" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "add",
            "version": "1.0.0",
            "description": "Adds two numbers together",
            "tags": ["math"],
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "add": {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": "Adds two numbers together", "tags": ["math"], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": False,
            }
        }
        harness.save_registry(reg)
        results = harness.search_capabilities("add")
        assert len(results) == 1
        assert results[0]["name"] == "add"

    def test_search_by_description(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        version_dir = tools / "add" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "add",
            "version": "1.0.0",
            "description": "Adds two numbers together",
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "add": {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": "Adds two numbers together", "tags": [], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": False,
            }
        }
        harness.save_registry(reg)
        results = harness.search_capabilities("numbers")
        assert len(results) == 1

    def test_search_hides_quarantined(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        version_dir = tools / "bad" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "bad",
            "version": "1.0.0",
            "description": "Bad tool",
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "bad": {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": "Bad tool", "tags": [], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": True,
            }
        }
        harness.save_registry(reg)
        results = harness.search_capabilities("bad")
        assert len(results) == 0

    def test_search_limit(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Create 3 tools
        for name in ["add", "sub", "mul"]:
            version_dir = tools / name / "1.0.0"
            version_dir.mkdir(parents=True)
            (version_dir / "manifest.json").write_text(json.dumps({
                "name": name,
                "version": "1.0.0",
                "description": f"{name} operation",
            }))
            (version_dir / "main.py").write_text("print('ok')")

        reg = {}
        for name in ["add", "sub", "mul"]:
            reg[name] = {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": f"{name} operation", "tags": [], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": False,
            }
        harness.save_registry(reg)
        results = harness.search_capabilities("operation", limit=2)
        assert len(results) == 2


class TestExecuteDispatcher:
    def test_execute_ws_write(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        result = harness.execute("ws_write", {"path": "test.txt", "content": "hello"})
        assert "result" in result
        assert (workspace / "test.txt").read_text() == "hello"

    def test_execute_ws_list(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        (workspace / "a.txt").write_text("a")
        (workspace / "b.txt").write_text("b")
        result = harness.execute("ws_list", {"path": ""})
        assert "result" in result
        assert "a.txt" in result["result"]
        assert "b.txt" in result["result"]

    def test_execute_search_capabilities(self, harness_dir):
        import harness
        result = harness.execute("search_capabilities", {"query": "test"})
        assert "result" in result
        assert isinstance(result["result"], list)

    def test_execute_unknown_tool(self, harness_dir):
        import harness
        result = harness.execute("nonexistent_tool", {"x": 1})
        assert "error" in result

    def test_execute_never_raises(self, harness_dir):
        """execute() must never raise on any input."""
        import harness
        # Various edge cases
        harness.execute("", {})
        harness.execute("ws_write", {})
        harness.execute("ws_list", {})
        harness.execute("search_capabilities", {})
        harness.execute("submit_tool", {})
        harness.execute("rollback_tool", {})
        harness.execute("run_code", {})
        # All should return dicts, never raise


class TestWorkspacePathEscape:
    def test_ws_write_block_parent_escape(self, harness_dir):
        import harness
        result = harness.execute("ws_write", {"path": "../x", "content": "bad"})
        assert "error" in result
        assert result["error"] == "path_escape"

    def test_ws_write_block_absolute_path(self, harness_dir):
        import harness
        result = harness.execute("ws_write", {"path": "/etc/passwd", "content": "bad"})
        assert "error" in result

    def test_ws_list_block_parent_escape(self, harness_dir):
        import harness
        result = harness.execute("ws_list", {"path": "../x"})
        assert "error" in result
        assert result["error"] == "path_escape"


class TestSubmitTool:
    def test_submit_basic(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "add",
            "version": "1.0.0",
            "description": "Adds two numbers together",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
            "examples": [{"input": {"a": 1, "b": 2}, "output": {"result": 3}}],
            "permissions": {},
        }
        code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
        result = harness.submit_tool(manifest, code)
        assert result["ok"] is True
        assert result["status"] == "accepted"

        # Check files exist
        assert (tools / "add" / "1.0.0" / "manifest.json").is_file()
        assert (tools / "add" / "1.0.0" / "main.py").is_file()

    def test_submit_invalid_name(self, harness_dir):
        import harness
        manifest = {"name": "Bad Name!", "version": "1.0.0", "description": "Test tool description"}
        result = harness.submit_tool(manifest, "print('ok')")
        assert result["ok"] is False
        assert result["status"] == "rejected"

    def test_submit_short_description(self, harness_dir):
        import harness
        manifest = {"name": "good", "version": "1.0.0", "description": "Short"}
        result = harness.submit_tool(manifest, "print('ok')")
        assert result["ok"] is False

    def test_submit_version_order(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Submit v1.0.0
        manifest = {
            "name": "tool1",
            "version": "1.0.0",
            "description": "First version of tool1",
            "permissions": {},
        }
        harness.submit_tool(manifest, "print('ok')")

        # Submit v0.9.0 — should be rejected
        manifest["version"] = "0.9.0"
        manifest["description"] = "Older version of tool1"
        result = harness.submit_tool(manifest, "print('ok')")
        assert result["ok"] is False

    def test_submit_duplicate_version(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "tool2",
            "version": "1.0.0",
            "description": "Tool2 first version",
            "permissions": {},
        }
        harness.submit_tool(manifest, "print('ok')")
        result = harness.submit_tool(manifest, "print('ok')")
        assert result["ok"] is False

    def test_submit_dry_run(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "drytool",
            "version": "1.0.0",
            "description": "Dry run test tool description",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            "examples": [{"input": {}, "output": {"ok": True}}],
            "permissions": {},
        }
        result = harness.submit_tool(manifest, "import sys, json; print(json.dumps({'ok': True}))", dry_run=True)
        assert result["ok"] is True
        # Should NOT install
        assert not (tools / "drytool").exists()


class TestRollbackTool:
    def test_rollback_not_found(self, harness_dir):
        import harness
        result = harness.execute("rollback_tool", {"name": "nonexistent"})
        assert "error" in result

    def test_rollback_clears_quarantine(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        # Create a tool with 2 versions
        for ver in ["1.0.0", "2.0.0"]:
            version_dir = tools / "rbtool" / ver
            version_dir.mkdir(parents=True)
            (version_dir / "manifest.json").write_text(json.dumps({
                "name": "rbtool",
                "version": ver,
                "description": f"Version {ver}",
            }))
            (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "rbtool": {
                "active": "2.0.0",
                "versions": {
                    "1.0.0": {"description": "v1", "tags": [], "permissions": {}, "healthy": True},
                    "2.0.0": {"description": "v2", "tags": [], "permissions": {}, "healthy": True},
                },
                "stats": {"calls": 0, "failures": 0, "consecutive_failures": 0, "last_used": None},
                "quarantined": True,
            }
        }
        harness.save_registry(reg)

        result = harness.execute("rollback_tool", {"name": "rbtool"})
        assert "result" in result
        # Check quarantine cleared
        loaded = harness.load_registry()
        assert loaded["rbtool"]["quarantined"] is False


class TestDescribe:
    def test_describe_empty(self, harness_dir):
        import harness
        desc = harness.describe()
        assert "no installed capabilities" in desc

    def test_describe_with_tools(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        version_dir = tools / "add" / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(json.dumps({
            "name": "add",
            "version": "1.0.0",
            "description": "Adds two numbers",
        }))
        (version_dir / "main.py").write_text("print('ok')")

        reg = {
            "add": {
                "active": "1.0.0",
                "versions": {"1.0.0": {"description": "Adds two numbers", "tags": [], "permissions": {}, "healthy": True}},
                "stats": {},
                "quarantined": False,
            }
        }
        harness.save_registry(reg)
        desc = harness.describe()
        assert "add" in desc
        assert "1.0.0" in desc


class TestVersionImmutability:
    def test_cannot_overwrite_version(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "immtool",
            "version": "1.0.0",
            "description": "Immutable tool test",
            "permissions": {},
        }
        harness.submit_tool(manifest, "print('v1')")
        # Try to submit the same version again
        result = harness.submit_tool(manifest, "print('v2')")
        assert result["ok"] is False

    def test_new_version_creates_new_dir(self, harness_dir):
        tmp_path, tools, workspace = harness_dir
        import harness
        manifest = {
            "name": "multiver",
            "version": "1.0.0",
            "description": "Multi version tool description",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {"v": {"type": "integer"}}},
            "examples": [{"input": {}, "output": {"v": 1}}],
            "permissions": {},
        }
        harness.submit_tool(manifest, "import sys, json; print(json.dumps({'v': 1}))")
        manifest["version"] = "2.0.0"
        manifest["description"] = "Multi version tool v2 description"
        manifest["output_schema"] = {"type": "object", "properties": {"v": {"type": "integer"}}}
        manifest["examples"] = [{"input": {}, "output": {"v": 2}}]
        harness.submit_tool(manifest, "import sys, json; print(json.dumps({'v': 2}))")

        assert (tools / "multiver" / "1.0.0" / "main.py").is_file()
        assert (tools / "multiver" / "2.0.0" / "main.py").is_file()
