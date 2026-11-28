"""Debug: why does Gemini return an empty turn after the first tool result?"""

import asyncio
import json
import os

import httpx
from pathlib import Path
from keys import load_keys
from openai import AsyncOpenAI

load_keys(verbose=False)


async def log_request(request: httpx.Request) -> None:
    body = json.loads(request.content or b"{}")
    msgs = body.get("messages", [])
    n = len(list(Path(".").glob("req_*.json")))
    Path(f"req_{n:02d}.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
    print(f">>> {len(msgs)} msgs, tools={len(body.get('tools', []))}, keys={sorted(k for k in body if k not in ('messages', 'tools'))}")
    for m in msgs[-3:]:
        print("   ", json.dumps(m)[:300])


async def log_response(response: httpx.Response) -> None:
    await response.aread()
    try:
        d = response.json()
        ch = d["choices"][0]
        print(f"<<< {response.status_code} finish={ch.get('finish_reason')} content={str(ch['message'].get('content'))[:200]!r} tool_calls={json.dumps(ch['message'].get('tool_calls'))[:300]} usage={d.get('usage')}")
    except Exception as exc:  # noqa: BLE001
        print("<<<", response.status_code, response.text[:500], exc)


async def main() -> None:
    import agent_run
    from sandbox import DockerSandbox

    http = httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}, timeout=120)
    oai = AsyncOpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url="https://generativelanguage.googleapis.com/v1beta/openai/", http_client=http)

    orig = agent_run.OpenAIChatCompletionClient

    def patched(**kw):
        kw.pop("api_key", None)
        kw.pop("base_url", None)
        return orig(async_client=oai, **kw)

    agent_run.OpenAIChatCompletionClient = patched
    sbx = DockerSandbox("maf-spike-ws-debug")
    sbx.start()
    ref = {"sbx": sbx}
    stats = agent_run.new_stats()
    agent, store = agent_run.build_agent(ref, "gemini-2.5-flash", stats, allow_network_tool=False)
    await store.write_file("sales.csv", agent_run.make_csv(1, 50, ["north", "south"], ["2026-01", "2026-02"]))
    resp = await agent.run(agent_run.T1, session=agent.create_session())
    print("FINAL:", repr(resp.text[:500]))
    sbx.stop()


asyncio.run(main())
