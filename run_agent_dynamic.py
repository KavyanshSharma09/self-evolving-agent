import sys
import shutil
from pathlib import Path
from agent_loop import run_agent

def clean_tools(exclude: set[str] | None = None):
    """Remove all tool folders except those in *exclude* (defaults to keep 'create_tool')."""
    tools_dir = Path(__file__).resolve().parent / "tools"
    if not tools_dir.is_dir():
        return
    exclude = exclude or {"create_tool"}
    for item in tools_dir.iterdir():
        if item.is_dir() and item.name not in exclude:
            shutil.rmtree(item)

def read_task() -> str:
    """Read a multi-line task from stdin. Blank line sends, 'exit' quits."""
    print("Task (blank line to send, 'exit' to quit):")
    lines = []
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            raise SystemExit
        if line.strip().lower() == "exit":
            raise SystemExit
        if not line.strip():
            break
        lines.append(line)
    return " ".join(lines)


if __name__ == "__main__":
    # Clean any previously created tools
    clean_tools(exclude=set())
    print("Enter a task description (or type 'exit' / 'quit' to stop).")
    while True:
        try:
            task = read_task().strip()
        except SystemExit:
            break
        if not task:
            continue
        if task.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break
        print(f"\nRunning agent on: {task}\n")
        result = run_agent(task)
        print("\nFINAL:", result)
