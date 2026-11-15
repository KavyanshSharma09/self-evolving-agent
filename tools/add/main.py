import json
import sys

data = json.load(sys.stdin)
print(json.dumps({"result": data["a"] + data["b"]}))