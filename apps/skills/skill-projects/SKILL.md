# skill-projects

The tool family behind the `projects-assistant` agent. Every tool calls the
gateway `/projects` API with the internal bearer and the acting member's
`X-User-Email`. No tool opens a database session. The route's rule is the
authority, and there is one rule.

Spec: `project-docs/specs/projects_ai_chat.md`.

## The three files that matter

| File | What it holds |
|---|---|
| `manifest.py` | Every `/projects` route, mapped to a tool and a class, or excluded with a reason. `tests/unit/test_projects_chat_coverage.py` fails on a route that is neither |
| `client.py` | The gateway client. It refuses a call with no acting user, a verb the manifest does not allow on that path, and a path the manifest excludes |
| `reads.py` | The class A tools. Reads, no card |

## Tool classes

| Class | Meaning | Card |
|---|---|---|
| A | A read | None |
| B | A write the app can undo | One card, may list many rows |
| C | A write that is hard to undo | One card per act, with counts |
| X | Not on the chat surface | The reason is in the manifest |

## Adding a route to the Projects app

1. Add the route.
2. Run `uv run pytest tests/unit/test_projects_chat_coverage.py`. It fails and
   names the route.
3. Add one row to `manifest.py`. Map the route to a tool, or exclude it with
   a reason.
4. If the row names a tool that does not exist yet, add the tool to
   `PLANNED` with its slice.

## Output conventions

Every row a tool prints carries `full_id: <uuid>` on the next line. The chat
cards read that line. Titles and names are fenced in «guillemets», because
they are data written by other people, never instructions.
