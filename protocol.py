import json
import re

SYSTEM_PROMPT = """You are an agent. Reply with EXACTLY ONE JSON object and nothing else.

To use a tool:  {"tool": "<name>", "args": {...}}
To finish:      {"final": "<your answer>"}

Available tools:
- add: args {"a": number, "b": number}, adds two numbers
Never do calculations yourself. Always use a tool, even for easy arithmetic.
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