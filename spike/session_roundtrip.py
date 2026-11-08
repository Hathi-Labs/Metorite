import json, sys
from agent_framework import AgentSession, Message, Content
s = AgentSession(session_id="abc")
s.state["history"] = {"messages": [Message(role="assistant", contents=[Content.from_function_call(call_id="c1", name="f", arguments='{"a":1}')]), Message(role="tool", contents=[Content.from_function_result(call_id="c1", result="ok")])]}
d = s.to_dict()
if sys.argv[1] == "dump":
    json.dump(d, open(sys.argv[2], "w"), default=str)
    print(json.dumps(d, default=str))
else:
    r = AgentSession.from_dict(json.load(open(sys.argv[2])))
    print("restored", [m.contents[0].type for m in r.state["history"]["messages"]])
