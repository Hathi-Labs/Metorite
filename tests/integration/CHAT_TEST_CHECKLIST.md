# Metorite Chat -- Manual UI Test Checklist

Run these tests in the Control Plane (http://187.127.179.143:3001) against both
MAF agents (e.g. `task-manager`) and Copilot SDK agents (e.g. `agent-project-manager`).

---

## 1. SSE Streaming
- [ ] **MAF** -- Send a message, text streams token-by-token
- [ ] **MAF** -- RUN_STARTED appears immediately (no blank screen)
- [ ] **MAF** -- RUN_FINISHED marks completion
- [ ] **Copilot** -- Send a message, text streams token-by-token
- [ ] **Copilot** -- RUN_STARTED appears immediately
- [ ] **Copilot** -- RUN_FINISHED marks completion
- [ ] TEXT_MESSAGE_END correctly frames each message
- [ ] No "chat dies without output" after thinking stream

## 2. Thinking / Consciousness Stream
- [ ] Switch think mode to "Max"
- [ ] **Copilot** -- ThinkingContainer shows reasoning deltas
- [ ] Thinking collapses/expands on click
- [ ] Thinking does NOT appear in the main message text

## 3. Tool Calls
- [ ] TOOL_CALL_START renders tool name with spinner
- [ ] TOOL_CALL_ARGS shows arguments (JSON or string)
- [ ] TOOL_CALL_RESULT shows result (green check / red X)
- [ ] Tool calls expand/collapse on click
- [ ] TOOL_CALL_PARTIAL shows live terminal output (if applicable)
- [ ] Multiple tool calls in one turn all visible

## 4. HITL -- ask_questions (custom)
- [ ] Agent calls `ask_questions` -> ElicitationCard appears inline
- [ ] Card shows header, question text, options
- [ ] **Blocking path**: option selected -> answer sent -> agent continues in SAME stream
- [ ] **Non-blocking fallback**: answer sent as next chat message
- [ ] `request_id` is present in the CUSTOM event (check browser Network tab)
- [ ] Freeform text input works
- [ ] Multiple questions in one card work
- [ ] Agent produces text AFTER user answers (no "chat dies" bug)

## 5. HITL -- ask_user (native Copilot SDK)
- [ ] Agent calls `ask_user` -> interactive card appears
- [ ] Answer posted -> agent resumes in same stream
- [ ] No extra message turn created for the answer

## 6. Stop
- [ ] Stop button visible and clickable during streaming
- [ ] Clicking stop terminates the stream
- [ ] Partial response displayed after stop

## 7. Steer
- [ ] Steer input visible during streaming
- [ ] Typing a steer message adjusts agent direction
- [ ] Agent responds to steer in the same stream

## 8. Queue / Conversation History
- [ ] Send multiple messages -> all appear in correct order
- [ ] Page refresh -> conversation history restored
- [ ] Thread list shows all conversations
- [ ] Switching threads loads correct history

## 9. Loading / Context Spinner
- [ ] Loading spinner appears while agent is thinking
- [ ] Spinner disappears when first text token arrives
- [ ] Progress updates shown during long tool calls
- [ ] Idle state after completion (no stuck spinner)

## 10. Model Switching
- [ ] Change model dropdown mid-thread
- [ ] Next message uses the selected model
- [ ] Conversation context preserved after model switch
- [ ] BYOK models (groq/*, deepseek/*) route correctly

## 11. Memory Tools
- [ ] Agent calls `remember` -> results visible in stream
- [ ] Agent calls `save_memory` -> confirmation visible
- [ ] Agent calls `recall_timeline` -> timeline data returned
- [ ] Memory persists across conversations (refresh and re-check)

## 12. Delegation
- [ ] Agent calls `call_agent` -> sub-agent events in stream
- [ ] Sub-agent response appears in parent stream
- [ ] `call_agents_parallel` runs multiple sub-agents
- [ ] `call_agent_background` returns immediately

## 13. Web Tools
- [ ] `web_search` returns real search results
- [ ] `fetch_page` fetches and summarizes a URL

## 14. Artifacts
- [ ] `write_artifact` creates a file
- [ ] Download link appears in the chat message
- [ ] File visible in Files sidebar
- [ ] Clicking download link works (file downloads)

## 15. Todo Panel
- [ ] `manage_todo_list` creates todo items
- [ ] Todo panel renders above chat input
- [ ] Status changes (in-progress -> completed) update live
- [ ] Todo panel persists across page refresh

## 16. Commit Detection
- [ ] Agent makes file changes -> commits appear in inbox
- [ ] Inbox shows commit diff
- [ ] Approve button pushes commit
- [ ] Reject button discards commit

## 17. Edge Cases
- [ ] Very long message (>2000 chars) handles correctly
- [ ] Special characters in message (emoji, Unicode) work
- [ ] Rapid consecutive messages don't corrupt state
- [ ] Browser back/forward during streaming recovers
- [ ] Network disconnect/reconnect during streaming recovers
- [ ] Empty message does not crash the agent
- [ ] Invalid model name shows error, not blank screen

## 18. Stream Relay / Reconnection
- [ ] Every SSE event has `_stream_id`
- [ ] `GET /agent/run/{thread_id}/reconnect` returns events
- [ ] Reconnecting client sees missed events
- [ ] RUN_FINISHED always lands before stream marked inactive

---

## Test Data (copy-paste prompts)

### Basic streaming
```
Say hello in exactly 3 words.
```

### Tool call -- web search
```
Search the web for 'Metorite orchestration platform' and tell me the first result title.
```

### HITL -- ask_questions
```
Before proceeding, use ask_questions to ask me: {"questions":[{"header":"Confirm","question":"Should I continue?","options":[{"label":"Yes"},{"label":"No"}]}]}
```

### HITL -- ask_user
```
Ask me a single yes/no question using ask_user.
```

### Memory -- remember
```
Use the remember tool to recall facts about user 'test-user-123'. Then tell me what you found.
```

### Memory -- save
```
Use the save_memory tool to save this fact: 'test-user-123 prefers dark mode'. Then confirm the save.
```

### Delegation
```
Use call_agent to ask task-manager: 'What is your purpose?' Then summarize its response in one sentence.
```

### Artifact
```
Use write_artifact to create a file called 'outputs/test_artifact.md' with content '# Test Artifact'. Tell me the download URL.
```

### Todo
```
Create a todo list with 3 items for planning a party: 1) Send invitations, 2) Buy supplies, 3) Decorate. Use manage_todo_list. Then mark item 1 as completed.
```

### Conversation continuity
Turn 1:
```
Remember this number: 4273. Just say 'OK, remembered 4273'.
```
Turn 2 (same thread):
```
What number did I ask you to remember earlier? Reply with just the number.
```
