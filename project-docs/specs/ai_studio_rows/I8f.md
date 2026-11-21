# I8f

Tier: STD

Gate: AGENT-SAFE. After I8a. Dark behind `MCP_OAUTH_ENABLED`

## What

**The MCP OAuth return** (task 3219). An MCP server that asks for an OAuth consent (IN-8) returns to the step of Studio after the consent, through the same guard of `return`

## Done when

The consent of an OAuth MCP server returns to the IDE, and the line reads connected
