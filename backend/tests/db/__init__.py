"""Round-trip tests: a run survives storage unchanged (Phase 9).

The contract these tests protect is the one the API depends on: `GET
/api/investigations/{id}` must return exactly what the `POST` that stored the run
returned. That holds only if a stored investigation rebuilds into the same
`InvestigationState` the graph produced, so the assertion throughout is
`semantic_view(reloaded) == semantic_view(live)` — Phase 7's own definition of
"the same run", with only the wall-clock metadata excluded.

Anything weaker would pass while storage quietly lost a field, and the loss
would surface as a report that no longer matches the investigation behind it.
"""
