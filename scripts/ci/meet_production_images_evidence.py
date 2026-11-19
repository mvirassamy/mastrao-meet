#!/usr/bin/env python3
"""Verify the selected Meet production images without mutating a registry."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PRODUCTION_REGISTRY = "rg.fr-par.scw.cloud/mastrao-production"
TRIVY_VERSION = "0.74.0"
EVIDENCE_LIFETIME = dt.timedelta(minutes=60)
IMAGE_INDEX_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
IMAGE_MANIFEST_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}


@dataclass(frozen=True)
class Selection:
    role: str
    repository: str
    digest: str
    source_commit: str

    @property
    def reference(self) -> str:
        return f"{self.repository}@{self.digest}"


SELECTIONS = {
    "meet-frontend": Selection(
        role="meet-frontend",
        repository=f"{PRODUCTION_REGISTRY}/meet-frontend",
        digest=(
            "sha256:0b0b3caa5b43708d61255dcd094ffa026e6140021b2195bc1194a58b28fe5689"
        ),
        source_commit="e34c6aeb75b57637722cae8b9569e07604d3cc20",
    ),
    "meet-api": Selection(
        role="meet-api",
        repository=f"{PRODUCTION_REGISTRY}/meet-backend",
        digest=(
            "sha256:dc2bd06cfda721bbf0c9177414dde545e641205339be0b4a9e51339e8469d3a8"
        ),
        source_commit="e501a32a6c7a93d2155a5481154a06ec7a76bff8",
    ),
}


def selection_for(role: str) -> Selection:
    try:
        return SELECTIONS[role]
    except KeyError as error:
        raise ValueError("unsupported Meet production image role") from error


def select(role: str) -> str:
    selection = selection_for(role)
    return "".join(
        (
            f"role={selection.role}\n",
            f"reference={selection.reference}\n",
            f"root_digest={selection.digest}\n",
            f"source_commit={selection.source_commit}\n",
        )
    )


def inspect_layout(
    *, role: str, layout: Path, scan_layout: Path
) -> dict[str, object]:
    selection = selection_for(role)
    index = _read_json(layout / "index.json", "OCI layout index")
    roots = _descriptors(index.get("manifests"), "OCI layout roots")
    if len(roots) != 1 or roots[0]["digest"] != selection.digest:
        raise ValueError("OCI layout root does not match the selected image")

    root_descriptor = roots[0]
    root = _read_descriptor_json(layout, root_descriptor, "selected image root")
    root_media_type = root.get("mediaType") or root_descriptor.get("mediaType")
    _verify_descriptor_graph(layout, root_descriptor, root)
    runtime = _runtime_descriptor(root_descriptor, root, root_media_type)
    runtime_manifest = _read_descriptor_json(layout, runtime, "runtime manifest")
    if runtime_manifest.get("mediaType") not in IMAGE_MANIFEST_MEDIA_TYPES:
        raise ValueError("runtime descriptor is not an image manifest")

    config = _required_descriptor(runtime_manifest.get("config"), "runtime config")
    layers = _descriptors(runtime_manifest.get("layers"), "runtime layers")
    _read_descriptor_bytes(layout, config, "runtime config")
    for layer in layers:
        _read_descriptor_bytes(layout, layer, "runtime layer")

    _prepare_scan_layout(layout, scan_layout, runtime)
    return {
        "schemaVersion": 1,
        "kind": "mastrao.meet.production-image-oci-readback.v1",
        "status": "EXACT_PRODUCTION_DIGEST_READ_BACK",
        "role": selection.role,
        "reference": selection.reference,
        "rootDigest": selection.digest,
        "runtimeManifestDigest": runtime["digest"],
        "sourceCommit": selection.source_commit,
        "configDigest": config["digest"],
        "layerDigests": [layer["digest"] for layer in layers],
        "registryMutationPerformed": False,
    }


def render_evidence(
    *,
    role: str,
    graph_path: Path,
    vulnerability_report_path: Path,
    sbom_path: Path,
    trivy_version_path: Path,
    scanned_at: dt.datetime,
) -> dict[str, object]:
    selection = selection_for(role)
    graph = _read_json(graph_path, "OCI graph")
    if (
        graph.get("status") != "EXACT_PRODUCTION_DIGEST_READ_BACK"
        or graph.get("role") != role
        or graph.get("rootDigest") != selection.digest
        or graph.get("reference") != selection.reference
    ):
        raise ValueError("OCI graph does not match the selected image")

    version = _read_json(trivy_version_path, "Trivy version")
    if version.get("Version") != TRIVY_VERSION:
        raise ValueError("Trivy version does not match the evidence policy")
    database_valid_until = _validate_vulnerability_database(version, scanned_at)

    report = _read_json(vulnerability_report_path, "Trivy report")
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict) or metadata.get("ImageID") != graph.get(
        "configDigest"
    ):
        raise ValueError("Trivy report is not bound to the selected image")
    findings = _blocking_findings(report)
    if findings:
        raise ValueError("selected image contains fixed HIGH or CRITICAL findings")

    sbom = _read_json(sbom_path, "CycloneDX SBOM")
    if sbom.get("bomFormat") != "CycloneDX" or not isinstance(
        sbom.get("components"), list
    ):
        raise ValueError("CycloneDX SBOM is malformed")

    now = dt.datetime.now(dt.timezone.utc)
    if scanned_at > now + dt.timedelta(minutes=1):
        raise ValueError("scan time is in the future")
    valid_until = min(scanned_at + EVIDENCE_LIFETIME, database_valid_until)
    if valid_until <= now:
        raise ValueError("Meet production image evidence has expired")

    return {
        "schemaVersion": 1,
        "kind": "mastrao.meet.production-image-evidence.v1",
        "status": "READ_ONLY_IMAGE_EVIDENCE_CAPTURED_SIGNATURE_NOT_PROVEN",
        "role": role,
        "reference": selection.reference,
        "rootDigest": selection.digest,
        "sourceCommit": selection.source_commit,
        "scannedAt": _format_time(scanned_at),
        "validUntil": _format_time(valid_until),
        "scan": {
            "scanner": "Trivy",
            "version": TRIVY_VERSION,
            "fixedHighOrCriticalFindings": 0,
            "reportSha256": _sha256(vulnerability_report_path.read_bytes()),
        },
        "sbom": {
            "format": "CycloneDX",
            "componentCount": len(sbom["components"]),
            "sha256": _sha256(sbom_path.read_bytes()),
        },
        "registryReadback": {
            "status": "VERIFIED",
            "reference": selection.reference,
            "rootDigest": selection.digest,
            "ociGraphSha256": _sha256(graph_path.read_bytes()),
        },
        "signatureVerification": {
            "status": "NOT_PROVEN",
            "reason": "NO_MEET_IMAGE_SIGNATURE_TRUST_POLICY",
        },
        "registryMutationPerformed": False,
        "publicationPerformed": False,
        "deploymentPerformed": False,
    }


def _runtime_descriptor(
    root_descriptor: dict[str, object],
    root: dict[str, object],
    root_media_type: object,
) -> dict[str, object]:
    if root_media_type in IMAGE_MANIFEST_MEDIA_TYPES:
        return root_descriptor
    if root_media_type not in IMAGE_INDEX_MEDIA_TYPES:
        raise ValueError("selected image root media type is unsupported")

    manifests = _descriptors(root.get("manifests"), "selected image manifests")
    runtimes = [descriptor for descriptor in manifests if _is_linux_amd64(descriptor)]
    if len(runtimes) != 1:
        raise ValueError("selected image must contain one linux/amd64 runtime")
    return runtimes[0]


def _verify_descriptor_graph(
    layout: Path,
    descriptor: dict[str, object],
    document: dict[str, object] | None = None,
) -> None:
    document = document or _read_descriptor_json(layout, descriptor, "OCI document")
    media_type = document.get("mediaType") or descriptor.get("mediaType")
    if media_type in IMAGE_INDEX_MEDIA_TYPES:
        for child in _descriptors(document.get("manifests"), "OCI index manifests"):
            _verify_descriptor_graph(layout, child)
        return
    if media_type not in IMAGE_MANIFEST_MEDIA_TYPES:
        raise ValueError("OCI graph contains an unsupported document")
    config = _required_descriptor(document.get("config"), "OCI manifest config")
    _read_descriptor_bytes(layout, config, "OCI manifest config")
    for layer in _descriptors(document.get("layers"), "OCI manifest layers"):
        _read_descriptor_bytes(layout, layer, "OCI manifest layer")


def _is_linux_amd64(descriptor: dict[str, object]) -> bool:
    annotations = descriptor.get("annotations")
    if isinstance(annotations, dict) and annotations.get(
        "vnd.docker.reference.type"
    ) == "attestation-manifest":
        return False
    platform = descriptor.get("platform")
    return isinstance(platform, dict) and (
        platform.get("os"),
        platform.get("architecture"),
    ) == ("linux", "amd64")


def _prepare_scan_layout(
    source: Path, destination: Path, runtime: dict[str, object]
) -> None:
    if destination.exists():
        raise ValueError("runtime scan layout already exists")
    destination.mkdir(parents=True)
    shutil.copytree(source / "blobs", destination / "blobs")
    shutil.copyfile(source / "oci-layout", destination / "oci-layout")
    _write_json(
        destination / "index.json",
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [runtime],
        },
    )


def _blocking_findings(report: dict[str, object]) -> list[dict[str, object]]:
    results = report.get("Results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise ValueError("Trivy report results are malformed")
    findings = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Trivy report result is malformed")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValueError("Trivy vulnerabilities are malformed")
        for finding in vulnerabilities:
            if not isinstance(finding, dict):
                raise ValueError("Trivy vulnerability is malformed")
            if finding.get("Severity") in {"HIGH", "CRITICAL"}:
                findings.append(finding)
    return findings


def _validate_vulnerability_database(
    version: dict[str, object], scanned_at: dt.datetime
) -> dt.datetime:
    database = version.get("VulnerabilityDB")
    if not isinstance(database, dict):
        raise ValueError("Trivy vulnerability database metadata is missing")
    updated_at = _parse_time_value(database.get("UpdatedAt"), "database update time")
    next_update = _parse_time_value(
        database.get("NextUpdate"), "database next update time"
    )
    downloaded_at = _parse_time_value(
        database.get("DownloadedAt"), "database download time"
    )
    if not updated_at <= downloaded_at <= scanned_at + dt.timedelta(minutes=5):
        raise ValueError("Trivy vulnerability database timestamps are inconsistent")
    if next_update <= scanned_at:
        raise ValueError("Trivy vulnerability database is expired")
    return next_update


def _descriptors(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} are malformed")
    return [_required_descriptor(item, label) for item in value]


def _required_descriptor(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is malformed")
    digest = value.get("digest")
    size = value.get("size")
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        raise ValueError(f"{label} digest is malformed")
    if not isinstance(size, int) or size < 0:
        raise ValueError(f"{label} size is malformed")
    return value


def _read_descriptor_json(
    layout: Path, descriptor: dict[str, object], label: str
) -> dict[str, object]:
    raw = _read_descriptor_bytes(layout, descriptor, label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _read_descriptor_bytes(
    layout: Path, descriptor: dict[str, object], label: str
) -> bytes:
    digest = descriptor["digest"]
    raw = (layout / "blobs" / "sha256" / digest.removeprefix("sha256:")).read_bytes()
    if _sha256(raw) != digest or len(raw) != descriptor["size"]:
        raise ValueError(f"{label} bytes do not match their descriptor")
    return raw


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _parse_time(value: str) -> dt.datetime:
    return _parse_time_value(value, "scan time")


def _parse_time_value(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is missing")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def _format_time(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    selection = commands.add_parser("select")
    selection.add_argument("--role", required=True)

    inspection = commands.add_parser("inspect")
    inspection.add_argument("--role", required=True)
    inspection.add_argument("--layout", required=True, type=Path)
    inspection.add_argument("--scan-layout", required=True, type=Path)
    inspection.add_argument("--output", required=True, type=Path)

    render = commands.add_parser("render")
    render.add_argument("--role", required=True)
    render.add_argument("--graph", required=True, type=Path)
    render.add_argument("--vulnerability-report", required=True, type=Path)
    render.add_argument("--sbom", required=True, type=Path)
    render.add_argument("--trivy-version", required=True, type=Path)
    render.add_argument("--scanned-at", required=True)
    render.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "select":
        sys.stdout.write(select(args.role))
        return
    if args.command == "inspect":
        _write_json(
            args.output,
            inspect_layout(
                role=args.role,
                layout=args.layout,
                scan_layout=args.scan_layout,
            ),
        )
        return
    _write_json(
        args.output,
        render_evidence(
            role=args.role,
            graph_path=args.graph,
            vulnerability_report_path=args.vulnerability_report,
            sbom_path=args.sbom,
            trivy_version_path=args.trivy_version,
            scanned_at=_parse_time(args.scanned_at),
        ),
    )


if __name__ == "__main__":
    main()
