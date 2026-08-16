# Learning Resources — Building & Deploying an Agent Orchestration Platform

> A field guide to how **Metorite** is built, and — more importantly — how *you* could build and deploy a similar system on the open internet.

This folder is a self-contained course. It uses Metorite (Fracktal Works' self-mutating,
multi-agent "operating system for a company") as the worked example, but every chapter is written
so the **patterns transfer** to any agent-orchestration product you might build. Where we describe a
Metorite-specific choice, we call out the general principle behind it so you can make your own
call.

Metorite itself, in one sentence: *a FastAPI core listens for events, dynamically loads
specialist AI agents from Git repositories, runs them on the Microsoft Agent Framework, streams
their reasoning to a Next.js control plane over the AG-UI protocol, gates every external write behind
human approval, and — when an agent fails — spawns a GitHub Copilot SDK sandbox that opens a PR to
fix the agent's own code.*

---

## Who this is for

- Engineers who can write Python and TypeScript but have **never assembled a full agent platform**
  (orchestration + tool calling + a streaming UI + auth + CI/CD + a VPS).
- People who have built a single chatbot and want to understand the **jump to a multi-agent,
  event-driven, production system**.
- Architects evaluating the building blocks: Microsoft Agent Framework (MAF), LiteLLM, MCP,
  CopilotKit/AG-UI, the GitHub Copilot SDK.

You do **not** need prior experience with any of those named libraries. Each chapter introduces the
concept first, then shows how Metorite uses it.

---

## How to read this

Read `01` and `02` first — they give you the mental model. After that the chapters are independent;
jump to whichever layer you're building.

| # | Chapter | What you'll learn |
|---|---------|-------------------|
| 00 | **[Glossary](./00-glossary.md)** | Every acronym and product name in one place. Skim it, then refer back. |
| 01 | **[System Architecture Overview](./01-architecture-overview.md)** | The whole system on one page: components, data flow, the C4 view, the core design principles. Start here. |
| 02 | **[How the Web App Is Built (Frontend + Backend)](./02-web-application.md)** | The monorepo layout, the FastAPI backend, the Next.js frontend, and how they talk to each other. |
| 03 | **[Hosting on a VPS](./03-vps-hosting.md)** | How a multi-service app runs on a single cheap Linux box: Docker for stateful infra, systemd for the apps, Caddy for TLS + reverse proxy. |
| 04 | **[CI/CD — Push-to-Deploy](./04-cicd.md)** | The GitHub Actions pipeline: lint → test-gate → SSH deploy → migrate → rebuild → smoke test. How to build your own. |
| 05 | **[Authentication & OAuth](./05-auth-and-oauth.md)** | User login (Microsoft Entra / Google SSO), gateway API auth, and the harder problem: OAuth *to third-party APIs* on the user's behalf, with token storage and refresh. |
| 06 | **[The Orchestration System](./06-orchestration.md)** | The heart of the platform: event routing, the dynamic agent loader, running agents on MAF, HITL approval gates, and the self-mutation loop. |
| 07 | **[How an Agent Works](./07-agents.md)** | Anatomy of an agent: instructions, tools, context providers, memory. The agent run loop. Single-agent vs multi-agent (handoff/concurrent/group-chat). |
| 08 | **[Tool Calling](./08-tool-calling.md)** | How an LLM "does things": the tool-call loop, defining tools, schemas, execution, and results. The universal pattern under every framework. |
| 09 | **[LLM Routing with LiteLLM](./09-litellm-routing.md)** | One API for every model provider. Tiered routing (cheap → powerful), context-window fitting, model fallback, BYOK, and cost control. |
| 10 | **[MCP & Connecting to External Apps](./10-mcp-and-integrations.md)** | The Model Context Protocol as a universal adapter, plus webhooks/REST/OAuth patterns for ClickUp, Zoho, Gmail, WhatsApp, and friends. |
| 11 | **[Microsoft Agent Framework (MAF)](./11-microsoft-agent-framework.md)** | Deep dive on the runtime that ties it together: agents, workflows, context providers, the AG-UI endpoint, OTel. |
| 12 | **[GitHub Copilot SDK & Self-Mutation](./12-copilot-sdk-self-mutation.md)** | Using a CLI-driven coding agent as a sandboxed tool runtime and as the engine that lets the system fix its own bugs. |
| 13 | **[AG-UI & Generative UI](./13-ag-ui-and-generative-ui.md)** | The streaming protocol between agents and the browser, and how agents render interactive UI (approval cards, live tool output, artifacts) inside the chat. |
| 14 | **[Build Your Own — A Minimal Blueprint](./14-build-your-own.md)** | A stripped-down recipe: the smallest thing that is still recognisably "an agent orchestration app," and the order to build it in. |

---

## The one-paragraph mental model

Everything in this course is a variation on a single loop:

> **An event arrives → the system decides which agent should handle it → it loads that agent's code and
> credentials → the agent runs an LLM in a loop, calling tools to read and write the outside world →
> risky actions pause for a human to approve → the whole run is streamed live to a UI and written to an
> audit log.**

Hold that loop in your head. Each chapter zooms into one arc of it.

---

*Generated as living documentation. If the code and these docs disagree, the code wins — but please
open a PR to fix the doc.*
