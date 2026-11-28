import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv
import openai
from openai.types.chat import ChatCompletionMessageParam

load_dotenv()
gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
gemini_base_url = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")
client = (
    None
    if gemini_api_key
    else openai.OpenAI(api_key=os.getenv("API_KEY"), base_url=os.getenv("BASE_URL"))
)
model = os.getenv("MODEL") or ("gemini-flash-lite-latest" if gemini_api_key else None)


def _ask_gemini(
    messages: list[ChatCompletionMessageParam],
    system: str,
    generation_config: dict[str, object] | None = None,
) -> str:
    turns = []
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        elif not isinstance(content, str):
            content = str(content)
        turns.append(
            {
                "type": "model_output" if role == "assistant" else "user_input",
                "content": [{"type": "text", "text": content}],
            }
        )

    model_name = (model or "gemini-flash-lite-latest").removeprefix("models/")
    payload = {"model": model_name, "input": turns or "", "store": False}
    if system:
        payload["system_instruction"] = system
    if generation_config:
        payload["generation_config"] = generation_config
    request = urllib.request.Request(
        f"{gemini_base_url}/interactions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": gemini_api_key or "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = ""
        if gemini_api_key:
            detail = detail.replace(gemini_api_key, "[REDACTED]")
        raise RuntimeError(
            f"Gemini API returned HTTP {error.code}: {detail or error.reason}"
        ) from error
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    for step in reversed(data.get("steps", [])):
        if step.get("type") != "model_output":
            continue
        content = step.get("content", [])
        if isinstance(content, str):
            return content
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return "".join(
        item.get("text", "")
        for item in data.get("outputs", [])
        if isinstance(item, dict) and item.get("type") == "text"
    )


def ask(
    messages: list[ChatCompletionMessageParam],
    system: str = "You are a helpful assistant.",
    generation_config: dict[str, object] | None = None,
) -> str:
    """Send the conversation to the model, return its reply as plain text."""
    global model
    if gemini_api_key:
        return _ask_gemini(messages, system, generation_config)
    if model is None:
        model = client.models.list().data[0].id
    full: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system},
        *messages,
    ]
    response = client.chat.completions.create(model=model, messages=full)
    return response.choices[0].message.content or ""
    


if __name__ == "__main__":
    history: list[ChatCompletionMessageParam] = []
    while True:
        history.append({"role": "user", "content": input("you> ")})
        reply = ask(history)
        history.append({"role": "assistant", "content": reply})
        print("ai>", reply)