"""MCP OAuth for an org connection: the flow (WS-54 IN-8a, D98).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.3 to §3.6.6 and
§3.6.11, rows O4, O5 and O12.

Mutation table (each mutation is made by hand, and the named test must fail).
This table is committed BEFORE the code, so it names the fence first.

| # | Mutation | Test that fails |
|---|---|---|
| M1 | the revoke hop calls ``outbound_guard.request`` (it allows ``http``) and not ``request_https`` | ``test_mcp_oauth_guard.py::test_each_hop_refuses[revoke-http]``, ``test_mcp_oauth_guard.py::test_no_oauth_module_fetches_by_itself`` |
| M2 | discovery sends to the ``resource_metadata`` URL without a check (a private address goes out) | ``test_mcp_oauth_guard.py::test_each_hop_refuses[prm-private]`` |
| M3 | the callback does not compare the org of the state with the session | ``test_mcp_oauth_state.py::test_a_state_of_another_org_is_400`` |
| M4 | the callback reads the pending flow with GET and does not delete it | ``test_mcp_oauth_state.py::test_a_replayed_state_is_400_and_sends_no_token_request`` |
| M5 | the consent URL sends ``plain`` when the server offers it | ``test_mcp_oauth_flow.py::test_the_consent_url_sends_s256_only`` |
| M6 | the callback skips the ``iss`` check | ``test_mcp_oauth_flow.py::test_an_iss_mismatch_is_400_before_the_token_request`` |
| M7 | the DCR ``client_secret`` goes in ``provider_meta`` | ``test_mcp_oauth_secrets.py::test_no_secret_reaches_a_log_a_response_a_url_or_a_column`` |
| M8 | the flag dependency is gone from the start and the callback | ``test_mcp_oauth_flow.py::test_flag_off_every_route_is_404`` |
| M9 | the runtime reader ignores the flag, so an OAuth row reaches a run | ``test_mcp_oauth_flow.py::test_flag_off_a_run_leaves_out_an_oauth_row`` |
| M10 | the store write of the credential loses its org predicate | ``test_mcp_oauth_org_scope_r8.py::test_the_store_predicate_holds_on_a_session_that_bypasses_rls`` |
| M11 | the ``client_id`` of the CIMD document is not its own URL | ``test_mcp_oauth_cimd.py::test_the_client_id_is_the_url_of_the_document`` |
| M12 | the callback does not compare the member of the state with the session | ``test_mcp_oauth_state.py::test_a_state_of_another_member_is_400`` |
| M13 | the code exchange sends no ``code_verifier`` | ``test_mcp_oauth_flow.py::test_a_wrong_verifier_fails_and_saves_nothing`` |
"""
