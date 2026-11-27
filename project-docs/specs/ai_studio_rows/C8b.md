# C8b

Tier: HIGH

Gate: AGENT-SAFE. After J2

## What

**The role map at install, and the reach of a member who is not the owner.** It waits for J2 (task 1200). An install maps each role of `roles` to subjects of the D12 grammar: an email, `group:<slug>` or `org` (`acb_auth.permissions.normalize_grant_subject`). WS-PKG owns the writer of the map in the install store (`package_host/store.py`). WS-STU owns the grammar (`studio_access.py`). The door resolves the roles of the member in the session of the second check, and again at a spend (`invoke._viewer`). Groups come from `routes/projects/core.resolve_visibility_for`, or from one shared read. Never write a second group query, and do not copy the `data:org:read` shortcut of `resolve_visibility`

## Done when

R8: a member of a mapped `group:<slug>` gets the grant of its role. A member in no mapped subject reads nothing. A member who leaves the group loses the rows at the next call. No role comes from a body. `test_studio_row_rules_r8.py` gains these cases, and `test_studio_package_install_r8.py` holds the map
