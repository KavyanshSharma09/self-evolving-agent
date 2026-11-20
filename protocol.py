import json
import re

SYSTEM_PROMPT = """You are an agent. Reply with EXACTLY ONE JSON object and nothing else.

To use a tool:  {"tool": "<name>", "args": {...}}
To finish:      {"final": "<your answer>"}

Core tools (always available):
- ws_write: Write content to a file in the workspace. Args: path, content.
- ws_list: List files in a workspace directory. Args: path (optional).
- search_capabilities: Search for installed tools by keyword. Args: query, limit.
- submit_tool: Submit a new or updated tool. Args: manifest, code.
- rollback_tool: Roll back a tool to a previous version. Args: name, version.
- run_code: Run a Python snippet in the sandbox. Args: code, timeout.

Decision policy — follow this order for every task:
1. ALWAYS call search_capabilities first to find existing tools.
2. REUSE: If a registry match fits the schema and example, use it directly.
3. COMPOSE: If the task splits into existing tools, create a workflow tool.
4. ADAPT: If a near-match exists, write a higher version of the same tool name (must pass old examples).
5. CREATE: If no match exists, create a new tool via submit_tool.
6. For one-off work with no reuse value, use run_code instead of creating a tool.

Report which operation (REUSE/COMPOSE/ADAPT/CREATE/run_code) you used in your final answer.
Never do calculations yourself. Always use a tool when one fits.
"""


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


def build_system_prompt(tools_text: str = "") -> str:
    """Build the system prompt. tools_text is optional extra info about installed tools.
    The model discovers tools via search_capabilities, not by listing them all."""
    prompt = SYSTEM_PROMPT
    if tools_text:
        prompt += f"\n\nInstalled capabilities:\n{tools_text}"
    return prompt