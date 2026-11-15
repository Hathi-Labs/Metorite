# J2b

Tier: HIGH

Gate: AGENT-SAFE

## What

**The share and the approval.** Four slices: J2b-1 (the approval for the org, built), J2b-2 (the share with people), J2b-3 (the chat twins) and J2b-4 (Needs you). The approval writer of PKG-11b-2 (task 2174) is `request_org_approval`, `approve_version` and `reject_version` in `gateway/studio/packages_store.py`. WS-STU J2 owns this one writer of `app_versions.review_status`, and WS-PKG calls it. Each slice calls those functions and builds no copy of them. **The owner's answer of 2026-11-13 (answer 5918 on task 1200): path A.** The org install of PKG-8b stays the one share to the org, and an approved live version gates it. `promote_to_org` (PKG-11b-1) is not built for now. **The owner's defaults:** (1) path A. (2) The approver reads the FULL pending manifest first. A true diff of two versions comes in a later slice. (3) An approval survives a pause, and the org install refuses a paused package. (4) The owner cannot approve their own share (PKG-Q10). So production needs a second admin beside the owner. Task 1707 is built, and migration 413 gives `admin` the right `studio:publish`

## Done when

The done-when of J2b-1 to J2b-4
