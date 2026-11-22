#!/usr/bin/env bash
# Restart the local stack with the scripted model and the flags for the demos.
cd /c/wt-chat-tof-demos
export DEV_UP_ORGS_ON="CHAT_CARD_REVISE_ORGS CHAT_CARD_PRECONDITION_ORGS STUDIO_SKILL_OFFER_ORGS STUDIO_CHAT_TOOLS_ORGS"
export DEV_UP_EXTRA_GATEWAY_ENV="GATEWAY_URL=http://127.0.0.1:8423 LITELLM_BASE_URL=http://127.0.0.1:9423 DEEPSEEK_API_BASE=http://127.0.0.1:9423 DEEPSEEK_API_KEY=fake-local-key KNOWLEDGE_ENABLED=true KNOWLEDGE_ORGS=* TOOL_PACKS=* TOOL_PACKS_ORGS=*"
bash scripts/dev_up.sh --stop >/dev/null 2>&1
timeout 500 bash scripts/dev_up.sh >/dev/null 2>&1
