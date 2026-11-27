"""
Sandbox — runs tool code in a separate process or Docker container.

This provides two execution backends:
1. Subprocess backend (default):
   Runs Python in a subprocess with timeout, restricted environment, and
   workspace confinement (overrides file I/O to stay within workspace).
2. Docker backend (enabled via USE_DOCKER=true):
   Full OS-level container isolation:
   --network none, --memory 256m, --cpus 0.5, --read-only root filesystem,
   --tmpfs /tmp:size=64m, and workspace mounted as the only writable volume.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Workspace confinement wrapper for subprocess backend
# ---------------------------------------------------------------------------

_WORKSPACE_WRAPPER = '''
import builtins
import os
import pathlib
import sys

_ALLOWED_DIR = os.path.realpath(os.environ["_HARNESS_WORKSPACE"])
_ENTRY_FILE = os.path.realpath(os.environ["_HARNESS_ENTRY"])

def _is_allowed(path):
    """Check if a resolved path is within the workspace or is the entry script."""
    try:
        p = pathlib.Path(path).resolve()
        w = pathlib.Path(_ALLOWED_DIR).resolve()
        if p == pathlib.Path(_ENTRY_FILE).resolve():
            return True
        return p == w or p.is_relative_to(w)
    except Exception:
        return False

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
with _original_open(_ENTRY_FILE, "r", encoding="utf-8") as _f:
    _code = _f.read()
exec(_code, {"__name__": "__main__", "__file__": _ENTRY_FILE})
'''


def is_docker_enabled() -> bool:
    """Return True if USE_DOCKER environment variable is enabled."""
    return os.environ.get("USE_DOCKER", "false").lower() in ("true", "1", "yes")


def is_docker_available() -> bool:
    """Check if Docker CLI and daemon are reachable."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        res = subprocess.run([docker_bin, "info"], capture_output=True, timeout=3.0)
        return res.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Subprocess execution functions
# ---------------------------------------------------------------------------

def _run_tool_subprocess(entry, payload: dict, timeout: float = 5.0, cwd: str = None, env_extra: dict = None) -> dict:
    """Run a Python file in a subprocess with workspace confinement."""
    work_dir = cwd
    cleanup = False
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True

    resolved_work_dir = str(pathlib.Path(work_dir).resolve())
    resolved_entry = str(pathlib.Path(entry).resolve())

    env = {
        "PYTHONIOENCODING": "utf-8",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "_HARNESS_WORKSPACE": resolved_work_dir,
        "_HARNESS_ENTRY": resolved_entry,
    }
    if os.name == "nt":
        env["PYTHONPATH"] = str(pathlib.Path(__file__).parent)
    if env_extra:
        env.update(env_extra)

    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    wrapper_script = tmp_dir / "wrapper.py"
    wrapper_script.write_text(_WORKSPACE_WRAPPER, encoding="utf-8")

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
                shutil.rmtree(work_dir, ignore_errors=True)
            except OSError:
                pass
        try:
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


def _run_raw_subprocess(entry, timeout: float = 10.0, cwd=None) -> dict:
    """Run a script in a subprocess with workspace confinement and return stdout/stderr as text."""
    work_dir = str(cwd) if cwd else None
    cleanup = False
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True

    resolved_work_dir = str(pathlib.Path(work_dir).resolve())
    resolved_entry = str(pathlib.Path(entry).resolve())

    env = {
        "PYTHONIOENCODING": "utf-8",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "_HARNESS_WORKSPACE": resolved_work_dir,
        "_HARNESS_ENTRY": resolved_entry,
    }

    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    wrapper_script = tmp_dir / "wrapper_raw.py"
    wrapper_script.write_text(_WORKSPACE_WRAPPER, encoding="utf-8")

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
                shutil.rmtree(work_dir, ignore_errors=True)
            except OSError:
                pass
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-3000:],
        "exit_code": proc.returncode,
    }


# ---------------------------------------------------------------------------
# Docker execution functions
# ---------------------------------------------------------------------------

def _run_tool_docker(entry, payload: dict, timeout: float = 5.0, cwd: str = None, env_extra: dict = None) -> dict:
    """Run a Python file in a hardened Docker container."""
    docker_bin = shutil.which("docker")
    if not docker_bin or not is_docker_available():
        return {"ok": False, "error": "docker_unavailable", "detail": "Docker CLI or daemon is not reachable"}

    work_dir = cwd
    cleanup = False
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True

    work_dir_abs = str(pathlib.Path(work_dir).resolve())
    entry_abs = str(pathlib.Path(entry).resolve())

    cmd = [
        docker_bin, "run", "--rm", "-i",
        "--network", "none",
        "--memory", "256m",
        "--cpus", "0.5",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "-v", f"{work_dir_abs}:/workspace:rw",
        "-v", f"{entry_abs}:/tool/main.py:ro",
        "-w", "/workspace",
    ]

    container_env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
    }
    if env_extra:
        container_env.update(env_extra)
    for k, v in container_env.items():
        cmd.extend(["-e", f"{k}={v}"])

    cmd.extend(["python:3.12-slim", "python", "-I", "/tool/main.py"])

    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout + 5.0,  # Startup allowance for container engine
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": f"exceeded {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": "docker_error", "detail": str(e)}
    finally:
        if cleanup:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
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


def _run_raw_docker(entry, timeout: float = 10.0, cwd=None) -> dict:
    """Run a script in a hardened Docker container and return text."""
    docker_bin = shutil.which("docker")
    if not docker_bin or not is_docker_available():
        return {"ok": False, "error": "docker_unavailable", "detail": "Docker CLI or daemon is not reachable", "exit_code": 1}

    work_dir = str(cwd) if cwd else None
    cleanup = False
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
        cleanup = True

    work_dir_abs = str(pathlib.Path(work_dir).resolve())
    entry_abs = str(pathlib.Path(entry).resolve())

    cmd = [
        docker_bin, "run", "--rm", "-i",
        "--network", "none",
        "--memory", "256m",
        "--cpus", "0.5",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "-v", f"{work_dir_abs}:/workspace:rw",
        "-v", f"{entry_abs}:/tool/snippet.py:ro",
        "-w", "/workspace",
    ]
    container_env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
    }
    for k, v in container_env.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.extend(["python:3.12-slim", "python", "-I", "/tool/snippet.py"])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5.0,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": f"exceeded {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": "docker_error", "detail": str(e), "exit_code": 1}
    finally:
        if cleanup:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except OSError:
                pass

    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-3000:],
        "exit_code": proc.returncode,
    }


# ---------------------------------------------------------------------------
# Public Entrypoints (router)
# ---------------------------------------------------------------------------

def run_tool(entry, payload: dict, timeout: float = 5.0, cwd: str = None, env_extra: dict = None) -> dict:
    """Run a Python file with workspace confinement.

    Routes to Docker backend if USE_DOCKER=true, else uses subprocess.

    Args:
        entry: Path to the Python file to run.
        payload: Dict sent as JSON on stdin.
        timeout: Max seconds before killing the process.
        cwd: Working directory (workspace).
        env_extra: Additional environment variables to merge.

    Returns:
        {"ok": True, "output": ...} on success.
        {"ok": False, "error": "timeout"|"crash"|"invalid_output", "detail": ...} on failure.
    """
    if is_docker_enabled():
        return _run_tool_docker(entry, payload, timeout=timeout, cwd=cwd, env_extra=env_extra)
    return _run_tool_subprocess(entry, payload, timeout=timeout, cwd=cwd, env_extra=env_extra)


def run_raw(entry, timeout: float = 10.0, cwd=None) -> dict:
    """Run a script with workspace confinement and return stdout/stderr as text.

    Routes to Docker backend if USE_DOCKER=true, else uses subprocess.
    """
    if is_docker_enabled():
        return _run_raw_docker(entry, timeout=timeout, cwd=cwd)
    return _run_raw_subprocess(entry, timeout=timeout, cwd=cwd)
