import json
from response import ask
from protocol import parser, build_system_prompt
from harness import execute, list_available_tools, load_registry, save_registry, _migrate_flat_tools

max_step = 7


def _ensure_migrated():
    """Run one-time migration of flat tools to versioned layout."""
    registry = load_registry()
    registry = _migrate_flat_tools(registry)
    save_registry(registry)


def run_agent(task: str) -> str:
    _ensure_migrated()
    history = [{"role": "user", "content": task}]
    searched = False  # Enforce search_capabilities before submit_tool

    for s in range(max_step):
        # Refresh available tools on every turn so newly created tools are visible
        available = list_available_tools()
        system = build_system_prompt(available)
        reply = ask(history, system=system)
        history.append({"role": "assistant", "content": reply})
        print(f"[step {s}] model: {reply}")

        try:
            action = parser(reply)
        except ValueError as e:
            history.append({"role": "user", "content": f"invalid reply: {e}. Reply with exactly one JSON object."})
            continue

        if "final" in action:
            return action["final"]

        name, args = action["tool"], action["args"]
        if name == "search_capabilities":
            searched = True
        # Enforce: must search before submitting a new tool
        if name == "submit_tool" and not searched:
            result = {"error": "search_first",
                      "detail": "Call search_capabilities before submit_tool to check a similar tool doesn't already exist."}
        else:
            result = execute(name, args)
        result_str = json.dumps(result)
        print(f"[step {s} tool result: {result_str}]")
        history.append({"role": "user", "content": f"tool result: {result_str}"})

    return "stopped: reached max steps"


if __name__ == "__main__":
    print("FINAL:", run_agent("What is 2 + 3? Then multiply that result by 4."))
