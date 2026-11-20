import sys, json, pathlib, os, datetime

def create_tool(payload):
    tool_name = payload["tool_name"]
    description = payload.get("description", "")
    args_schema = payload.get("args_schema", {})
    code = payload.get("code", "")
    prompt = payload.get("prompt", "")

    model_tools_dir = pathlib.Path(__file__).resolve().parents[2] / "tools_created_by_model"
    tool_path = model_tools_dir / tool_name
    if tool_path.exists():
        return {"ok": False, "error": "exists", "detail": f"Tool '{tool_name}' already exists"}

    tool_path.mkdir(parents=True, exist_ok=False)
    manifest = {
        "name": tool_name,
        "description": description,
        "args": args_schema,
    }
    (tool_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (tool_path / "main.py").write_text(code)

    return {"ok": True, "output": {"message": f"Tool '{tool_name}' created"}}

def main():
    payload = json.load(sys.stdin)
    result = create_tool(payload)
    print(json.dumps(result))

if __name__ == "__main__":
    main()
