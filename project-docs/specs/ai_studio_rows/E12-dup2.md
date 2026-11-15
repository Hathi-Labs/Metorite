# E12

Tier: HIGH

Gate: AGENT-SAFE. A run with no viewer waits on H-328

## What

**Events from every core app** (task 2379). Wave 2 (D202.7). `calendar.*`, `email.*`, `whatsapp.*` and `people.*` events on the one bus (`ingestion/event_hooks.py`), so a workflow can start on them. CRM-M14 owns the `crm.*` events, and E12 adds no second list

## Done when

A test lists each event of each core app. One change emits one event, and the event carries its org. A workflow starts on a Calendar event in the right org only
