#!/bin/sh
# Prove the canonical public route for the recording download contract.
#
# The product bootstrap is POST /recordings/access/ and the one-shot download
# is GET /recordings/download/current. Both must reach the Meet backend
# Service through the public Ingress. When only "/" exists, the SPA frontend
# nginx answers "405 Not Allowed" to the bootstrap POST, which is the defect
# this route closes.
set -eu

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
workdir="$(mktemp -d)"
cleanup() {
  rm -f "$workdir/values.yaml" "$workdir/ingress.yaml"
  rmdir "$workdir"
}
trap cleanup EXIT

render_chart() {
  values="$1"
  output="$2"
  if command -v helm >/dev/null 2>&1; then
    helm template meet "$root/meet" -f "$values" -s templates/ingress.yaml > "$output"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker run --rm -v "$root:/work" -v "$workdir:/values" alpine/helm:3.18.4 \
      template meet /work/meet -f "/values/$(basename "$values")" \
      -s templates/ingress.yaml > "$output"
    return
  fi
  echo "helm_or_docker_required" >&2
  exit 1
}

# Assert that the path table of one host block routes exactly as expected.
# Every rendered host must send "/" to the frontend and the backend families,
# including /recordings, to the backend Service.
assert_host_routes() {
  rendered="$1"
  host="$2"
  python3 - "$rendered" "$host" <<'PY'
import sys

rendered, host = sys.argv[1], sys.argv[2]

with open(rendered, encoding="utf8") as handle:
    lines = handle.read().splitlines()


def value_of(line):
    """Return the scalar after the first colon, without surrounding quotes."""

    return line.split(":", 1)[1].strip().strip('"')


# The rendered Ingress has a fixed, generated shape, so a small structural scan
# avoids depending on a YAML library being installed in every CI image.
annotations = []
in_annotations = False
for line in lines:
    if line.startswith("  annotations:"):
        in_annotations = True
        continue
    if in_annotations:
        if line.startswith("    ") and ":" in line:
            annotations.append(line.strip().split(":", 1)[0])
            continue
        in_annotations = False

routes = {}
current_host = None
path = pathType = name = None
for line in lines:
    stripped = line.strip()
    if stripped.startswith("- host:"):
        current_host = value_of(stripped)
        continue
    if current_host != host:
        continue
    if stripped.startswith("- path:"):
        path = value_of(stripped)
        pathType = name = None
        continue
    if stripped.startswith("pathType:"):
        pathType = value_of(stripped)
        continue
    if stripped.startswith("name:"):
        name = value_of(stripped)
        continue
    if stripped.startswith("number:") and path is not None:
        routes[path] = (pathType, name, int(value_of(stripped)))
        path = pathType = name = None

if not routes:
    print(f"host_missing:{host}", file=sys.stderr)
    raise SystemExit(1)

expected = {
    "/": ("Prefix", "meet-frontend", 80),
    "/api/": ("Prefix", "meet-backend", 80),
    "/external-api/": ("Prefix", "meet-backend", 80),
    "/recordings": ("Prefix", "meet-backend", 80),
}

for path, value in expected.items():
    if routes.get(path) != value:
        print(f"route_mismatch:{host}:{path}:{routes.get(path)}", file=sys.stderr)
        raise SystemExit(1)

# A rewrite would change the URI Django receives and can turn the bootstrap
# POST into a different request; the route must stay byte-for-byte faithful.
for annotation in annotations:
    if "rewrite-target" in annotation or "configuration-snippet" in annotation:
        print(f"forbidden_annotation:{annotation}", file=sys.stderr)
        raise SystemExit(1)
PY
}

cat > "$workdir/values.yaml" <<'YAML'
ingress:
  enabled: true
  className: nginx
  host: meet.mastrao.test
  path: /
  hosts:
    - visio.mastrao.test
    - meet-alt.mastrao.test
  tls:
    enabled: false
YAML

rendered="$workdir/ingress.yaml"
render_chart "$workdir/values.yaml" "$rendered"

# Primary host and every additional host must carry the identical route table.
assert_host_routes "$rendered" meet.mastrao.test
assert_host_routes "$rendered" visio.mastrao.test
assert_host_routes "$rendered" meet-alt.mastrao.test

# A neighbouring path must not be absorbed by the backend prefix; Prefix
# matching is element-wise, so /recordingsXYZ stays frontend traffic.
if grep -q "/recordingsXYZ" "$rendered"; then
  echo "unexpected_neighbour_route" >&2
  exit 1
fi

# Prove the declared prefix actually covers the two product endpoints and
# excludes neighbouring names, using Kubernetes Prefix (element-wise) matching
# rather than trusting a comment.
python3 - <<'PY'
prefix = "/recordings"


def matches(prefix, path):
    """Kubernetes Ingress Prefix matching: split on / and compare elements."""

    p = [e for e in prefix.split("/") if e]
    q = [e for e in path.split("/") if e]
    return len(q) >= len(p) and q[: len(p)] == p


must_match = ["/recordings", "/recordings/", "/recordings/access/", "/recordings/download/current"]
must_not_match = ["/recordingsXYZ", "/recordings-legacy/path", "/", "/api/v1.0/rooms/", "/room_abcdef"]

for path in must_match:
    if not matches(prefix, path):
        print(f"prefix_does_not_cover:{path}", file=__import__("sys").stderr)
        raise SystemExit(1)
for path in must_not_match:
    if matches(prefix, path):
        print(f"prefix_over_matches:{path}", file=__import__("sys").stderr)
        raise SystemExit(1)
PY

echo "recording_public_route_ok"
