"""Replay the captured first request against Gemini with tool-schema variants."""

import copy
import json
import os

from keys import load_keys
from openai import OpenAI

load_keys(verbose=False)
client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
import sys
base = json.load(open(sys.argv[1], encoding="utf-8"))


def inline_refs(schema):
    defs = schema.pop("$defs", {})

    def walk(n):
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(copy.deepcopy(defs[n["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in n.items()}
        if isinstance(n, list):
            return [walk(x) for x in n]
        return n

    return walk(schema)


def run(label, tools):
    body = copy.deepcopy(base)
    body["tools"] = tools
    for a in sys.argv:
        if a.startswith("--model="):
            body["model"] = a.split("=", 1)[1]
    body.pop("stream", None)
    r = client.chat.completions.create(**body)
    ch = r.choices[0]
    tc = [(t.function.name, t.function.arguments[:80]) for t in (ch.message.tool_calls or [])]
    print(f"{label}: finish={ch.finish_reason} tool_calls={tc} content={str(ch.message.content)[:80]!r}")


tools = base["tools"]
if "--as-sent" in sys.argv:
    for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 1):
        run(f"as-sent#{i}", tools)
if "--no-todo" in sys.argv:
    run("no-todo", [t for t in tools if not t["function"]["name"].startswith("todos_")])
inl = copy.deepcopy(tools)
for t in inl:
    t["function"]["parameters"] = inline_refs(t["function"]["parameters"])
for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 1):
    run(f"refs-inlined#{i}", inl)
