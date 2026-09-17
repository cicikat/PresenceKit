# Cross-repository protocol compatibility

The canonical fixture bundle is `tests/protocol_fixtures/v1/`. Desktop and
mobile consumer tests read that bundle through
`PRESENCEKIT_PROTOCOL_FIXTURES`; they do not maintain a second protocol truth.
`tests/protocol_fixtures/session-scope-proposed/` is a proposed-not-shipped
review bundle for session scope; it is not part of this frozen matrix until C
advertises `session_scope` and the three repositories pick it up together.

The machine-readable freeze is [.github/protocol-matrix.json](../.github/protocol-matrix.json).
The manually dispatched workflow is
`.github/workflows/protocol-compatibility.yml`. Every checkout uses an explicit
40-character SHA. A missing SHA, a checkout that resolves to another commit,
or a missing fixture is a hard failure.

The workflow requires read access to the three repositories through the
GitHub Actions checkout token. It does not print credentials. The current
artifact retention is 30 days and the artifact contains only the matrix and
fixture manifest; attach the workflow URL and suite logs to a release decision
when a run is used as evidence.

This workflow proves contract compatibility for the frozen three-repository
tuple. It does not prove Android instrumented tests, real-device lifecycle
behaviour, Tauri runtime behaviour, Live2D/WebGL, macOS, signing, or release
installation/upgrade acceptance.
