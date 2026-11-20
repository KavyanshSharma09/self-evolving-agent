import json
import pathlib
from datetime import datetime

TOOLS_DIR = pathlib.Path(__file__).resolve().parent / "tools"
REGISTRY_PATH = TOOLS_DIR / "_registry.json"


def load_registry():
    """Load the tool creation registry. Returns list of entries."""
    if REGISTRY_PATH.is_file():
        try:
            return json.loads(REGISTRY_PATH.read_text())
        except json.JSONDecodeError:
            return []
    return []


def add_registry_entry(name: str, prompt: str):
    """Add a new entry for a created tool with timestamp and originating prompt."""
    entry = {
        "name": name,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "prompt": prompt,
    }
    data = load_registry()
    data.append(entry)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2))
    return entry


def list_capabilities():
    """Return a human‑readable list of all known tools (manifest + registry)."""
    # load manifests
    caps = []
    for folder in TOOLS_DIR.iterdir():
        if not folder.is_dir():
            continue
        manifest_path = folder / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text())
                caps.append({
                    "name": manifest.get("name"),
                    "description": manifest.get("description"),
                    "args": manifest.get("args"),
                })
            except json.JSONDecodeError:
                continue
    # include registry entries for tools that may not have manifest yet (future)
    for entry in load_registry():
        if not any(c["name"] == entry["name"] for c in caps):
            caps.append({"name": entry["name"], "description": "(created dynamically)", "args": {}})
    return caps

