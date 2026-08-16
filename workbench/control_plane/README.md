# Metorite Control Plane (workbench/control_plane)

Next.js 16 + React 19 + Tailwind v4 shell for the Skill Workbench.

## Panes
- `/workflows` - Workflows app: visual automation editor (React Flow → compiled
  to MAF Workflows server-side) + Module Studio. Spec:
  `project-docs/specs/workflows_app.md`
- `/observability` - Audit / escalations / traces / spend
- (full pane list: `src/lib/nav.ts` — this README lists only the ones this
  file used to misdescribe)

## Dev
```bash
cd workbench/control_plane
npm install
npm run dev    # http://localhost:3001
```
Port `3001` is intentional - OpenHands self-host uses `3000`.

## Auth
NextAuth + Google SSO restricted to `@fracktal.in` lands in Phase 0.5.6. The shell is unauthenticated locally for now.

## CopilotKit
Pervasive AI chat (`useCopilotReadable` per pane) wires in at Phase 0.5.6.