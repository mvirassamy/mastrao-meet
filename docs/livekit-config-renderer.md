# LiveKit configuration renderer

The `livekit-render-production` image renders either a LiveKit server config or
an Egress config. It replaces the environment-specific inline Python command
previously carried by Kubernetes manifests.

The image has two explicit modes:

- `livekit` renders the server, RTC, Redis and webhook configuration;
- `egress` renders the Egress, Redis and session-limit configuration.

Every non-secret value is a required command argument. The LiveKit mode must
receive either one or more repeatable `--stun-server` arguments or the explicit
`--no-stun-servers` argument. API and Redis credentials are accepted only as
paths to mounted files. The renderer rejects missing, empty or `UNBOUND`
values. It verifies Redis over TLS with hostname and CA validation before it
writes anything.

The output is JSON, which is valid YAML for LiveKit and Egress. It is first
written to a temporary file in the destination directory, flushed, synced and
atomically renamed. The temporary and final files use mode `0600`. A failed
secret read, argument check or Redis TLS probe leaves an existing destination
unchanged.

The production-evidence workflow builds this target from its own protected
workflow commit into a local linux/amd64 OCI archive with `push: false`,
maximum BuildKit provenance, SBOM generation and the same pinned Trivy policy
as the other Room Audio images. Its receipt records
`BUILT_NOT_PUBLISHED_NOT_DEPLOYED`; the workflow neither publishes nor deploys
the image. After the archive passes receipt, complete-graph and scan validation,
the workflow retains those exact OCI bytes for one day with a record binding
their streaming SHA-256 to the receipt, graph and scan provenance inside one
flat, size-limited artifact. A future publication remains a separate
authorization and must use the retained digest-bound bytes without rebuilding.
