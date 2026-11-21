import json
import re

SYSTEM_PROMPT = """You are an agent. Reply with EXACTLY ONE JSON object and nothing else.

To use a tool:  {"tool": "<name>", "args": {...}}
To finish:      {"final": "<your answer>"}

Decision policy — follow this order for every task:
1. Review the available tools listed below.
2. REUSE: If an available tool fits the task, use it directly.
3. COMPOSE: If the task splits into existing tools, create a workflow tool.
4. ADAPT: If a near-match exists, write a higher version of the same tool name (must pass old examples).
5. CREATE: If no match exists, create a new tool via submit_tool.
6. For one-off work with no reuse value, use run_code instead of creating a tool.

Report which operation (REUSE/COMPOSE/ADAPT/CREATE/run_code) you used in your final answer.
Never do calculations yourself. Always use a tool when one fits.

HOW TO CREATE A TOOL
1. If your tool needs a sample file for examples, write it first with ws_write.
   Examples run with the workspace as the current folder.
2. Test your code with run_code first and copy the REAL output into the examples.
   Never guess expected outputs. Never put an output in an examples list that you
   haven't seen produced by run_code.
3. Call submit_tool with {"manifest": {...}, "code": "..."}.

Manifest fields: name, version ("1.0.0"), description (10+ chars), input_schema,
output_schema, examples: [{"input": {...}, "output": {...}}], permissions: {}, tags: [...].

IMPORTANT: input_schema must be proper JSON Schema, e.g.:
{"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
Do NOT use shorthand like {"path": "string"} — it will be rejected.

The code MUST follow this template exactly. Defining a function is not enough —
it is never called by itself:

import json, sys
data = json.load(sys.stdin)        # input arrives as ONE JSON object on stdin
# ... do the work using data["field"] etc ...
print(json.dumps(result))          # print exactly ONE JSON object and nothing else

WORKSPACE CONFINEMENT
All tools and run_code snippets are confined to the workspace directory.
You can only read and write files within the workspace. Accessing files outside
the workspace (e.g., "../.env", "C:\Windows") will raise a PermissionError.
The workspace is your sandbox — use ws_write to create files and ws_list to
see what's there.
"""


def _build_tools_json_block(tools_list: list) -> str:
    """Format the available tools list as a JSON block for the system prompt."""
    import json
    return json.dumps(tools_list, indent=2)


def parser(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"({.*})", text, re.DOTALL)
        if not match:
            raise ValueError(f"Could not find JSON object in text: {text}")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}")
    if not isinstance(obj, dict):
        raise ValueError(f"reply must be a JSON object: {obj}")
    if "final" in obj:
        return {"final": str(obj["final"])}
    if "tool" in obj and isinstance(obj.get("args", {}), dict):
        return {"tool": obj["tool"], "args": obj.get("args", {})}
    raise ValueError('object must have "tool" (with "args") or "final"')


def build_system_prompt(available_tools: list = None) -> str:
    """Build the system prompt with a JSON list of all available tools.
    available_tools should be a list of tool dicts (from harness.list_available_tools()).
    Each dict has: name, type, version, description, input_schema."""
    prompt = SYSTEM_PROMPT
    if available_tools:
        tools_json = _build_tools_json_block(available_tools)
        prompt += f"""

Available tools (JSON format):
{tools_json}

Use these tools by calling: {{"tool": "<name>", "args": {{...}}}}
New tools you create via submit_tool will be added to this list automatically."""
    return prompt