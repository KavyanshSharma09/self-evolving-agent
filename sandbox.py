"""
Sandbox — runs tool code in a separate subprocess.

This is defense-in-depth, not a real security boundary. Tools run via
subprocess with a timeout, limited environment, and a temporary working
directory. For stronger isolation, use the optional Docker backend.
"""

import json
import os
import subprocess
import sys
import tempfile
import pathlib


def run_tool(entry, payload: dict, timeout: float = 5.0, cwd: str = None, env_extra: dict = None) -> dict:
    """Run a Python file in a subprocess.

    Args:
        entry: Path to the Python file to run.
        payload: Dict sent as JSON on stdin.
        timeout: Max seconds before killing the process.
        cwd: Working directory for the subprocess. Defaults to a temp dir.
        env_extra: Additional environment variables to merge.

    Returns:
        {"ok": True, "output": ...} on success.
        {"ok": False, "error": "timeout"|"crash"|"invalid_output", "detail": ...} on failure.
    """
    # Build environment — always include Windows-safe vars
    env = {
        "PYTHONIDECODING": "utf-8",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
    }
    if os.name == "nt":
        env["PYTHONPATH"] = str(pathlib.Path(__file__).parent)
    if env_extra:
        env.update(env_extra)

    work_dir = cwd
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True
    else:
        cleanup = False

    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(pathlib.Path(entry).resolve())],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=work_dir,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": f"exceeded {timeout}s"}
    finally:
        if cleanup:
            try:
                import shutil
                shutil.rmtree(work_dir, ignore_errors=True)
            except OSError:
                pass

    if proc.returncode != 0:
        return {"ok": False, "error": "crash", "detail": proc.stderr[-500:]}
    try:
        return {"ok": True, "output": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_output", "detail": proc.stdout[:200]}