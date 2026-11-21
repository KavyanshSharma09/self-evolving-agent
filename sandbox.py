"""
Sandbox — runs tool code in a separate subprocess.

This is defense-in-depth, not a real security boundary. Tools run via
subprocess with a timeout, limited environment, and workspace confinement.
All file operations are restricted to the workspace directory.
For stronger isolation, use the optional Docker backend.
"""

import json
import os
import subprocess
import sys
import tempfile
import pathlib


# ---------------------------------------------------------------------------
# Workspace confinement wrapper
# ---------------------------------------------------------------------------

_WORKSPACE_WRAPPER = '''
import builtins
import os
import pathlib
import sys

_ALLOWED_DIR = os.path.realpath("{workspace_dir}")
_ENTRY_FILE = os.path.realpath("{entry_path}")

def _is_allowed(path):
    """Check if a resolved path is within the workspace or is the entry script."""
    return (path.startswith(_ALLOWED_DIR + os.sep) or
            path == _ALLOWED_DIR or
            path == _ENTRY_FILE)

# Override builtins.open to restrict file access
_original_open = builtins.open
def _restricted_open(file, mode="r", *args, **kwargs):
    if hasattr(file, "read"):
        return _original_open(file, mode, *args, **kwargs)
    resolved = os.path.realpath(str(file))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory. "
            "Tools can only access files within the workspace."
        )
    return _original_open(file, mode, *args, **kwargs)

builtins.open = _restricted_open

# Override os.open
_original_os_open = os.open
def _restricted_os_open(file, flags, *args, **kwargs):
    resolved = os.path.realpath(str(file))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory."
        )
    return _original_os_open(file, flags, *args, **kwargs)

os.open = _restricted_os_open

# Override pathlib.Path.open
_original_path_open = pathlib.Path.open
def _restricted_path_open(self, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(self))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory."
        )
    return _original_path_open(self, mode, *args, **kwargs)

pathlib.Path.open = _restricted_path_open

# Override pathlib.Path.read_text / read_bytes / write_text / write_bytes
for _attr in ("read_text", "read_bytes", "write_text", "write_bytes"):
    _orig = getattr(pathlib.Path, _attr, None)
    if _orig:
        def _make_wrapper(attr, orig):
            def wrapper(self, *a, **kw):
                resolved = os.path.realpath(str(self))
                if not _is_allowed(resolved):
                    raise PermissionError(
                        "Access denied: path '" + resolved + "' is outside the workspace directory."
                    )
                return orig(self, *a, **kw)
            return wrapper
        setattr(pathlib.Path, _attr, _make_wrapper(_attr, _orig))

# Now run the actual tool code
exec(open(_ENTRY_FILE).read(), {{"__name__": "__main__", "__file__": _ENTRY_FILE}})
'''


def _build_wrapper_script(workspace_dir: str, entry_path: str) -> str:
    """Return the workspace confinement wrapper script with the actual tool path injected."""
    return _WORKSPACE_WRAPPER.format(
        workspace_dir=workspace_dir.replace("\\", "\\\\"),
        entry_path=entry_path.replace("\\", "\\\\")
    )


def run_tool(entry, payload: dict, timeout: float = 5.0, cwd: str = None, env_extra: dict = None) -> dict:
    """Run a Python file in a subprocess with workspace confinement.

    Args:
        entry: Path to the Python file to run.
        payload: Dict sent as JSON on stdin.
        timeout: Max seconds before killing the process.
        cwd: Working directory (workspace) for the subprocess.
        env_extra: Additional environment variables to merge.

    Returns:
        {"ok": True, "output": ...} on success.
        {"ok": False, "error": "timeout"|"crash"|"invalid_output", "detail": ...} on failure.
    """
    # Build environment — always include Windows-safe vars
    env = {
        "PYTHONIOENCODING": "utf-8",
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

    # Write a wrapper script that enforces workspace confinement
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    wrapper_script = tmp_dir / "wrapper.py"
    wrapper_code = _build_wrapper_script(str(pathlib.Path(work_dir).resolve()), str(pathlib.Path(entry).resolve()))
    wrapper_script.write_text(wrapper_code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(wrapper_script)],
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
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    if proc.returncode != 0:
        return {"ok": False, "error": "crash", "detail": proc.stderr[-500:]}
    try:
        return {"ok": True, "output": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        detail = proc.stdout[:200] if proc.stdout.strip() else (
            "stdout was empty: the code must read JSON from stdin and print ONE JSON object "
            "(defining a function is not enough, it must be called)")
        return {"ok": False, "error": "invalid_output", "detail": detail}


# ---------------------------------------------------------------------------
# Workspace confinement wrapper for run_raw (no JSON stdin/stdout)
# ---------------------------------------------------------------------------

_WORKSPACE_WRAPPER_RAW = '''
import builtins
import os
import pathlib
import sys

_ALLOWED_DIR = os.path.realpath("{workspace_dir}")
_ENTRY_FILE = os.path.realpath("{entry_path}")

def _is_allowed(path):
    """Check if a resolved path is within the workspace or is the entry script."""
    return (path.startswith(_ALLOWED_DIR + os.sep) or
            path == _ALLOWED_DIR or
            path == _ENTRY_FILE)

# Override builtins.open to restrict file access
_original_open = builtins.open
def _restricted_open(file, mode="r", *args, **kwargs):
    if hasattr(file, "read"):
        return _original_open(file, mode, *args, **kwargs)
    resolved = os.path.realpath(str(file))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory. "
            "Code can only access files within the workspace."
        )
    return _original_open(file, mode, *args, **kwargs)

builtins.open = _restricted_open

# Override os.open
_original_os_open = os.open
def _restricted_os_open(file, flags, *args, **kwargs):
    resolved = os.path.realpath(str(file))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory."
        )
    return _original_os_open(file, flags, *args, **kwargs)

os.open = _restricted_os_open

# Override pathlib.Path.open
_original_path_open = pathlib.Path.open
def _restricted_path_open(self, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(self))
    if not _is_allowed(resolved):
        raise PermissionError(
            "Access denied: path '" + resolved + "' is outside the workspace directory."
        )
    return _original_path_open(self, mode, *args, **kwargs)

pathlib.Path.open = _restricted_path_open

# Override pathlib.Path.read_text / read_bytes / write_text / write_bytes
for _attr in ("read_text", "read_bytes", "write_text", "write_bytes"):
    _orig = getattr(pathlib.Path, _attr, None)
    if _orig:
        def _make_wrapper(attr, orig):
            def wrapper(self, *a, **kw):
                resolved = os.path.realpath(str(self))
                if not _is_allowed(resolved):
                    raise PermissionError(
                        "Access denied: path '" + resolved + "' is outside the workspace directory."
                    )
                return orig(self, *a, **kw)
            return wrapper
        setattr(pathlib.Path, _attr, _make_wrapper(_attr, _orig))

# Now run the actual code
exec(open(_ENTRY_FILE).read(), {{"__name__": "__main__", "__file__": _ENTRY_FILE}})
'''


def _build_wrapper_raw_script(workspace_dir: str, entry_path: str) -> str:
    """Return the workspace confinement wrapper for run_raw."""
    return _WORKSPACE_WRAPPER_RAW.format(
        workspace_dir=workspace_dir.replace("\\", "\\\\"),
        entry_path=entry_path.replace("\\", "\\\\")
    )


def run_raw(entry, timeout: float = 10.0, cwd=None) -> dict:
    """Run a script with workspace confinement and return its stdout/stderr as text.

    Unlike run_tool, this does not expect JSON output — it returns raw text.
    File access is restricted to the workspace directory.
    """
    env = {
        "PYTHONIOENCODING": "utf-8",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
    }

    work_dir = str(cwd) if cwd else None
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True
    else:
        cleanup = False

    # Write a wrapper script that enforces workspace confinement
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    wrapper_script = tmp_dir / "wrapper_raw.py"
    wrapper_code = _build_wrapper_raw_script(str(pathlib.Path(work_dir).resolve()), str(pathlib.Path(entry).resolve()))
    wrapper_script.write_text(wrapper_code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(wrapper_script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
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
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-3000:],
        "exit_code": proc.returncode,
    }