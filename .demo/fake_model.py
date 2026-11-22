"""Scripted OpenAI-compatible model for local demos. No network, no key. Logs each request."""
import json, sys, time, uuid, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[2] if len(sys.argv) > 2 else "fake_model.log"
SCRIPT = {}
try:
    SCRIPT = json.load(open(sys.argv[3]))
except Exception:
    pass

def decide(body):
    msgs = body.get("messages", [])
    tools = [t["function"]["name"] for t in body.get("tools") or []]
    last = msgs[-1] if msgs else {}
    users = [m for m in msgs if m.get("role") == "user"]
    utext = ""
    if users:
        c = users[-1].get("content")
        utext = c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
    for ch in SCRIPT.get("chains", []):
        if re.search(ch["when"], utext, re.I) and all(c["name"] in tools for c in ch["steps"][:1]):
            n = 0
            for m in reversed(msgs):
                if m.get("role") == "tool": n += 1
                elif m.get("role") == "user": break
            results = [str(m.get("content")) for m in msgs if m.get("role") == "tool"][-n:] if n else []
            if n < len(ch["steps"]):
                call = json.loads(json.dumps(ch["steps"][n]))
                blob = " ".join(results)
                ids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", blob)
                for k, v in list(call.get("args", {}).items()):
                    if isinstance(v, str) and v.startswith("{{id"):
                        idx = int(v[4:-2] or 0); call["args"][k] = ids[idx] if len(ids) > idx else "missing-id"
                return {"calls": [call]}
            return {"text": ch.get("final", "Done.")}
    if last.get("role") == "tool":
        return {"text": SCRIPT.get("after_tool", "Done. I used the tool and here is what it said.") + " [tool said: " + str(last.get("content"))[:900].replace("\n", " ") + "]"}
    for rule in SCRIPT.get("rules", []):
        if re.search(rule["when"], utext, re.I):
            names = [c["name"] for c in rule.get("calls", [])]
            if all(n in tools for n in names):
                return {"calls": rule["calls"]}
            continue
    return {"text": SCRIPT.get("default", "FAKE MODEL: no scripted rule for that message.")}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b'{"data":[]}')
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        d = decide(body)
        import os
        tag = '_'.join(sorted(t['function']['name'] for t in body.get('tools') or []))[:40]
        if body.get('tools') and not os.path.exists('tools_%d.json' % len(body['tools'])):
            json.dump(body['tools'], open('tools_%d.json' % len(body['tools']), 'w'))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "model": body.get("model"), "stream": body.get("stream"), "tools": [t["function"]["name"] for t in body.get("tools") or []], "last": (body.get("messages") or [{}])[-1], "decision": d, "has_skill": ("When to use" in json.dumps(body)) or ("please-look-through" in json.dumps(body)), "skill_snip": [m for m in re.findall(r".{80}(?:When to use|Please look through the knowledge spaces).{160}", json.dumps(body))][:2]})[:6000] + "\n")
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": body.get("model", "fake")}
        if d.get("calls"):
            tcs = [{"index": i, "id": "call_" + uuid.uuid4().hex[:10], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c.get("args", {}))}} for i, c in enumerate(d["calls"])]
            delta, fin = {"role": "assistant", "content": None, "tool_calls": tcs}, "tool_calls"
            msg = {"role": "assistant", "content": None, "tool_calls": [{k: v for k, v in t.items() if k != "index"} for t in tcs]}
        else:
            delta, fin = {"role": "assistant", "content": d["text"]}, "stop"
            msg = {"role": "assistant", "content": d["text"]}
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if body.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for ch in ({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                       {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": fin}], "usage": usage}):
                self.wfile.write(("data: " + json.dumps(ch) + "\n\n").encode()); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            out = {**base, "object": "chat.completion", "choices": [{"index": 0, "message": msg, "finish_reason": fin}], "usage": usage}
            data = json.dumps(out).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
