from response import ask
from protocol import parser, SYSTEM_PROMPT
from openai.types.chat import ChatCompletionMessageParam

tools = {
    "add": lambda a, b: a + b,
}

max_step  = 7

def run_agent(task: str) -> str:
    history: list[ChatCompletionMessageParam] = [{"role": "user", "content": task}]
    for s in range(max_step):
        reply = ask(history, system=SYSTEM_PROMPT)
        history.append({"role": "assistant", "content": reply})
        print(f"[step {s}] model: {reply}")

        try:
            action = parser(reply)
        except ValueError as e:
            history.append({"role": "user", "content": f"invalid reply: {e}. Reply with exactly one JSON object."})
            continue
        if "final" in action:
            return action["final"]
        name , agr = action["tool"], action["args"]
        if name not in tools:
            result = f"Error: unknown tool '{name}'. Available: {list(tools)}"
        else:
            try:
                result = str(tools[name](**agr))
            except Exception as e:
                 result = f"Error: {type(e).__name__}: {e}"
        print(f"[step {s} tool result: {result}]")
        history.append({"role": "user", "content": f"tool result: {result}"}) 
               
    return "stopped reached max steps"

if __name__ == "__main__":
    print("FINAL:", run_agent("What is 2 + 3? Then add 10 to that result"))