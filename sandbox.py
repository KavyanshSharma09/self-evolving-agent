import json
import os
import subprocess
import sys
import tempfile
import pathlib


def run_tool(entry, payload: dict , timeout: float = 5.0) -> dict:
    env = {"PYTHONIDECODING": "utf-8"}
    if os.name == "nt":
        env["PYTHONPATH"] = str(pathlib.Path(__file__).parent)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(pathlib.Path(entry).resolve())],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout", "detail": f"exceeded {timeout}s"}

    if proc.returncode != 0:
        return {"ok": False, "error": "crash", "detail": proc.stderr[-500:]}
    try:
        return {"ok": True, "output": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_output", "detail": proc.stdout[:200]}