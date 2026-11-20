import json
from harness import call_tool, describe
payload = {
    "tool_name": "fastapi_demo",
    "description": "FastAPI demo with GET /hello and POST /echo",
    "args_schema": {},
    "code": "import json\nfrom fastapi import FastAPI, Request\napp = FastAPI()\n\n@app.get('/hello')\ndef hello():\n    return {'message': 'hello world'}\n\n@app.post('/echo')\nasync def echo(request: Request):\n    data = await request.json()\n    return {'you_sent': data}\n\nif __name__ == '__main__':\n    import uvicorn\n    uvicorn.run(app, host='0.0.0.0', port=8000)\n",
    "prompt": "Create a FastAPI tool with GET /hello returning a greeting and POST /echo echoing JSON payload."
}
print('Create result:', call_tool('create_tool', payload))
print('Current tools:')
print(describe())

