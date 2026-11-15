import tempfile
from pathlib import Path
from sandbox import run_tool

def make(code: str) -> Path:
    f = Path(tempfile.mkdtemp()) / "main.py"
    f.write_text(code)
    return f

print("1 normal :", run_tool("tools/add/main.py", {"a": 2, "b": 3}))
print("2 loop   :", run_tool(make("while True: pass"), {}, timeout=2))
print("3 crash  :", run_tool(make("raise RuntimeError('boom')"), {}))
print("4 garbage:", run_tool(make("print('not json')"), {}))