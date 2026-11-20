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

if __name__ == "__main__":
    # Clean any previously created tools (keep create_tool which is required for dynamic creation)
    clean_tools()
    print("Enter a task description (or type 'exit' / 'quit' to stop).")
    while True:
        try:
            task = input("\nTask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not task:
            continue
        if task.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break
        print(f"\nRunning agent on: {task}\n")
        result = run_agent(task)
        print("\nFINAL:", result)
