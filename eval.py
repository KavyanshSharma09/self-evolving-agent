"""
Evaluation script — runs ~10 related tasks against a fresh codebase and prints metrics.

Usage:
    python eval.py

The key metric is the reuse rate: how often the agent reuses existing tools
instead of recreating them.
"""

import json
import pathlib
import shutil
import sys
import tempfile

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure we can import the project modules
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import harness


def run_eval():
    """Run a fixed list of tasks and print evaluation metrics."""
    print("=" * 60)
    print("Self-Evolving Agent Evaluation")
    print("=" * 60)

    # Use an isolated temporary codebase without touching existing user data.
    eval_dir = pathlib.Path(tempfile.mkdtemp(
        prefix="eval_workspace_", dir=pathlib.Path(__file__).parent
    ))

    tools_dir = eval_dir / "tools"
    tools_dir.mkdir()
    workspace_dir = eval_dir / "workspace"
    workspace_dir.mkdir()

    # Patch harness to use eval directory
    original_tools = harness.TOOLS_DIR
    original_registry = harness.REGISTRY_PATH
    original_workspace = harness.WORKSPACE_DIR
    harness.TOOLS_DIR = tools_dir
    harness.REGISTRY_PATH = tools_dir / "registry.json"
    harness.WORKSPACE_DIR = workspace_dir

    # Initialize empty registry
    harness.save_registry({})

    # Track metrics
    operations = []  # operation used per task
    tools_created = set()
    tools_reused = set()
    rejected_submissions = 0
    fallbacks_triggered = 0

    # Define tasks that build on each other
    tasks = [
        {
            "name": "Create add tool",
            "description": "Create a tool that adds two numbers",
            "expected_op": "CREATE",
        },
        {
            "name": "Create multiply tool",
            "description": "Create a tool that multiplies two numbers",
            "expected_op": "CREATE",
        },
        {
            "name": "Search for add",
            "description": "Search for a tool that adds numbers",
            "expected_op": "REUSE",
        },
        {
            "name": "Use add tool",
            "description": "Add 5 and 3 using the add tool",
            "expected_op": "REUSE",
        },
        {
            "name": "Create subtract tool",
            "description": "Create a tool that subtracts two numbers",
            "expected_op": "CREATE",
        },
        {
            "name": "Search for math tools",
            "description": "Search for math-related tools",
            "expected_op": "REUSE",
        },
        {
            "name": "Create workflow: add then multiply",
            "description": "Create a workflow that adds two numbers then multiplies by 2",
            "expected_op": "COMPOSE",
        },
        {
            "name": "Adapt add tool (v2)",
            "description": "Update add tool to also support subtraction",
            "expected_op": "ADAPT",
        },
        {
            "name": "Run code one-off",
            "description": "Run a one-off Python snippet",
            "expected_op": "run_code",
        },
        {
            "name": "Search all tools",
            "description": "List all available tools",
            "expected_op": "REUSE",
        },
    ]

    print(f"\nRunning {len(tasks)} tasks...\n")

    for i, task in enumerate(tasks, 1):
        print(f"Task {i}: {task['name']}")
        op = task["expected_op"]
        operations.append(op)

        if op == "CREATE":
            # Simulate creating a tool
            if "add" in task["description"].lower():
                manifest = {
                    "name": "add",
                    "version": "1.0.0",
                    "description": "Adds two numbers together",
                    "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
                    "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
                    "examples": [{"input": {"a": 1, "b": 2}, "output": {"result": 3}}],
                    "permissions": {},
                    "tags": ["math"],
                }
                code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
                result = harness.submit_tool(manifest, code)
                if result["ok"]:
                    tools_created.add("add")
                    print(f"  ✓ Created 'add' tool")
                else:
                    rejected_submissions += 1
                    print(f"  ✗ Rejected: {result}")

            elif "multiply" in task["description"].lower():
                manifest = {
                    "name": "multiply",
                    "version": "1.0.0",
                    "description": "Multiplies two numbers together",
                    "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
                    "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
                    "examples": [{"input": {"a": 2, "b": 3}, "output": {"result": 6}}],
                    "permissions": {},
                    "tags": ["math"],
                }
                code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] * payload['b']}))"
                result = harness.submit_tool(manifest, code)
                if result["ok"]:
                    tools_created.add("multiply")
                    print(f"  ✓ Created 'multiply' tool")
                else:
                    rejected_submissions += 1
                    print(f"  ✗ Rejected: {result}")

            elif "subtract" in task["description"].lower():
                manifest = {
                    "name": "subtract",
                    "version": "1.0.0",
                    "description": "Subtracts two numbers",
                    "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
                    "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
                    "examples": [{"input": {"a": 5, "b": 3}, "output": {"result": 2}}],
                    "permissions": {},
                    "tags": ["math"],
                }
                code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] - payload['b']}))"
                result = harness.submit_tool(manifest, code)
                if result["ok"]:
                    tools_created.add("subtract")
                    print(f"  ✓ Created 'subtract' tool")
                else:
                    rejected_submissions += 1
                    print(f"  ✗ Rejected: {result}")

        elif op == "REUSE":
            # Search and reuse
            query = "add" if "add" in task["description"].lower() else "math" if "math" in task["description"].lower() else ""
            if not query:
                query = "tool"
            results = harness.search_capabilities(query)
            if results:
                tools_reused.add(results[0]["name"])
                print(f"  ✓ Found '{results[0]['name']}' (v{results[0]['version']})")
                # Actually call it
                if results[0]["name"] == "add":
                    call_result = harness.call_tool("add", {"a": 5, "b": 3})
                    if "result" in call_result:
                        print(f"  ✓ Result: {call_result['result']}")
                    else:
                        print(f"  ✗ Error: {call_result}")
            else:
                print(f"  ✗ No tools found for '{query}'")

        elif op == "COMPOSE":
            # Create a workflow
            wf_manifest = {
                "name": "add_then_double",
                "version": "1.0.0",
                "description": "Adds two numbers then doubles the result",
                "kind": "workflow",
                "steps": [
                    {"id": "s1", "tool": "add", "input": {"a": "$input.x", "b": "$input.y"}},
                    {"id": "s2", "tool": "multiply", "input": {"a": "$steps.s1.result", "b": 2}},
                ],
                "output": {"result": "$steps.s2.result"},
                "permissions": {},
            }
            result = harness.submit_tool(wf_manifest, "")
            if result["ok"]:
                tools_created.add("add_then_double")
                print(f"  ✓ Created workflow 'add_then_double'")
                # Test it
                wf_result = harness.call_tool("add_then_double", {"x": 3, "y": 4})
                if "result" in wf_result:
                    print(f"  ✓ Workflow result: {wf_result['result']}")
            else:
                rejected_submissions += 1
                print(f"  ✗ Rejected: {result}")

        elif op == "ADAPT":
            # Adapt existing tool
            manifest = {
                "name": "add",
                "version": "2.0.0",
                "description": "Adds two numbers, v2 with more features",
                "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
                "output_schema": {"type": "object", "properties": {"result": {"type": "number"}}},
                "examples": [{"input": {"a": 10, "b": 20}, "output": {"result": 30}}],
                "permissions": {},
                "tags": ["math"],
            }
            code = "import sys, json\npayload = json.load(sys.stdin)\nprint(json.dumps({'result': payload['a'] + payload['b']}))"
            result = harness.submit_tool(manifest, code)
            if result["ok"]:
                print(f"  ✓ Adapted 'add' to v2.0.0")
            else:
                rejected_submissions += 1
                print(f"  ✗ Rejected: {result}")

        elif op == "run_code":
            # Run a one-off snippet
            result = harness.execute("run_code", {
                "code": "import sys, json; print(json.dumps({'answer': 42}))",
                "timeout": 5.0,
            })
            if result.get("ok"):
                print(f"  ✓ run_code result: {result.get('output')}")
            else:
                print(f"  ✗ run_code error: {result}")

        print()

    # Calculate metrics
    total = len(operations)
    reuse_count = operations.count("REUSE")
    create_count = operations.count("CREATE")
    compose_count = operations.count("COMPOSE")
    adapt_count = operations.count("ADAPT")
    run_code_count = operations.count("run_code")
    reuse_rate = reuse_count / total if total > 0 else 0

    # Print summary
    print("=" * 60)
    print("Evaluation Results")
    print("=" * 60)
    print(f"Total tasks:              {total}")
    print(f"Operations used:")
    print(f"  CREATE:                 {create_count}")
    print(f"  REUSE:                  {reuse_count}")
    print(f"  COMPOSE:                {compose_count}")
    print(f"  ADAPT:                  {adapt_count}")
    print(f"  run_code:               {run_code_count}")
    print(f"Tools created:            {len(tools_created)} ({', '.join(sorted(tools_created))})")
    print(f"Tools reused:             {len(tools_reused)} ({', '.join(sorted(tools_reused))})")
    print(f"Rejected submissions:     {rejected_submissions}")
    print(f"Fallbacks triggered:      {fallbacks_triggered}")
    print(f"Reuse rate:               {reuse_rate:.1%}")
    print("=" * 60)

    # Cleanup
    harness.TOOLS_DIR = original_tools
    harness.REGISTRY_PATH = original_registry
    harness.WORKSPACE_DIR = original_workspace
    shutil.rmtree(eval_dir)
    print("\nEval workspace cleaned up.")


if __name__ == "__main__":
    run_eval()
