from response import ask
from protocol import parser, SYSTEM_PROMPT

for question in ["What is 2 + 3?", "Say hello"]:
    reply = ask([{"role": "user", "content": question}], system=SYSTEM_PROMPT)
    print("RAW:   ", reply)
    try:
        print("PARSED:", parser(reply))
    except ValueError as e:
        print("ERROR: ", e)