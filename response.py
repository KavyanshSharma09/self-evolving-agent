import os
from dotenv import load_dotenv
import openai
from openai.types.chat import ChatCompletionMessageParam

load_dotenv()
client = openai.OpenAI(api_key=os.getenv("API_KEY"), base_url=os.getenv("BASE_URL"))
model = None

def ask(messages: list[ChatCompletionMessageParam], system: str = "You are a helpful assistant.") -> str:
    """Send the conversation to the model, return its reply as plain text."""
    global model
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