# Domain fixture harnesses

The root CI command runs standard-library contract tests:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin python3 -B -S \
  -m unittest discover -s tests -t . -v
```

The Django domain harnesses are separate direct-script entrypoints:

- `tests/live_observed_domain_fixture.py` (formerly
  `tests/test_live_observed_domain.py`).
- `tests/native_capture_domain_fixture.py` (formerly
  `tests/test_native_capture_domain.py`).

Their names intentionally do not match `test_*.py`. Root discovery does not
install the backend dependencies or configure Django. Each harness configures
Django in its own process; do not import both into one test-discovery process.
The adjacent `*_support.py` modules are helpers for these direct scripts.
All test classes and assertions are retained.

Run from the repository root using an already provisioned backend virtualenv.
Set `DOMAIN_FIXTURE_VENV` to that environment; these commands do not install it.
The following examples use its Python 3.13 site-packages explicitly. `-S`
disables site startup and `.pth` processing, and the empty environment avoids
inheriting application configuration. No `.env` or application settings file
is loaded: the helpers configure synthetic settings directly.

```sh
DOMAIN_FIXTURE_VENV="$PWD/src/backend/.venv"
env -i PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH="$DOMAIN_FIXTURE_VENV/lib/python3.13/site-packages" \
  "$DOMAIN_FIXTURE_VENV/bin/python" -B -S \
  tests/live_observed_domain_fixture.py LiveDomainWireTest
env -i PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH="$DOMAIN_FIXTURE_VENV/lib/python3.13/site-packages" \
  "$DOMAIN_FIXTURE_VENV/bin/python" -B -S \
  tests/native_capture_domain_fixture.py NativeCaptureDomainUnitTest
```

Without `--postgres-fixture`, the database backend is deliberately dummy.
Selecting the unit classes above runs no PostgreSQL lifecycle tests. Omitting
class names lists both unit and database tests, with database tests skipped.

For database lifecycle proofs, the paired Platform orchestrator must create a
disposable database using its existing `startLocalPostgres` helper. Run each
script in a separate process with `--postgres-fixture` and provide one JSON
object on stdin: `{"socket_directory":"/tmp/mastrao-shared-runtime-..."}`.
Close stdin after the JSON object so `json.load(sys.stdin)` receives EOF. For
example, invoke the command below with stdin supplied by the orchestrator;
repeat for the native script:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH="$DOMAIN_FIXTURE_VENV/lib/python3.13/site-packages" \
  "$DOMAIN_FIXTURE_VENV/bin/python" -B -S \
  tests/live_observed_domain_fixture.py --postgres-fixture
```

The helper validates the disposable socket directory. Do not substitute a
shared development or staging database. The native script retains its JSON
wire-assertion output after successful PostgreSQL runs. Renaming the scripts
does not change their stdin, argument, or output protocol. Platform callers
must use the new script names.

`live-observed-domain-fixtures.json` is a byte-exact copy of the paired
Platform canonical fixture at
`packages/meeting-runtime-contract/src/live-observed-domain-fixtures.json`.
Its SHA256 at packaging is
`1a20ba0cf887067201c2f5835d869dc681feee0b9e7df09845171f0bc7a2defc`.
The harness resolves it relative to its own file, so clean checkouts need no
sibling worktree. Update both copies together when the contract changes.
`synthetic_rtc_native_capture_domain_vectors.json` contains the existing
synthetic native-domain vectors; it is also required by the native harness.

These proofs use synthetic metadata, ephemeral signing keys and explicit Core
transport doubles. They do not call an ASR provider or qualify real audio,
billing, deployment, or staging behavior.
