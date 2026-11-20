from response import ask
from protocol import parser, build_system_prompt
from harness import execute, describe, load_registry, save_registry, _migrate_flat_tools

max_step = 7


def _ensure_migrated():
    """Run one-time migration of flat tools to versioned layout."""
    registry = load_registry()
    registry = _migrate_flat_tools(registry)
    save_registry(registry)


def run_agent(task: str) -> str:
    _ensure_migrated()
    system = build_system_prompt(describe())
    history = [{"role": "user", "content": task}]

    for s in range(max_step):
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
        result = execute(name, args)
        result_str = str(result)
        print(f"[step {s} tool result: {result_str}]")
        history.append({"role": "user", "content": f"tool result: {result_str}"})

    return "stopped: reached max steps"


if __name__ == "__main__":
    print("FINAL:", run_agent("What is 2 + 3? Then multiply that result by 4."))
