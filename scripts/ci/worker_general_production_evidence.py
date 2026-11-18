#!/usr/bin/env python3
"""Bind one immutable Meet backend candidate to production worker-general."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from scripts.ci.room_audio_production_evidence import (
    Candidate,
    expected_receipt,
    inspect_candidate,
    render_evidence,
)

SOURCE_COMMIT = "5ab1ed7e57ce6673aea88fdb9ce568cdfdbbfbf0"
SOURCE_TREE = "0730af167de659a3eef7137839927cc9c4329e0c"
SOURCE_ARCHIVE_SHA256 = (
    "76f8115409774e032efddd367aa6207b6f02dc3414d731fdb7b5b44847565edf"
)
RUN_ID = 44315610642
RUN_ATTEMPT = 1
ARTIFACT_ID = 14431683909
ROOT_DIGEST = "sha256:2840662cdc2c8363bced1c977c71c708f68f71c699ac4efaf991692f1e34b205"
RUNTIME_MANIFEST_DIGEST = (
    "sha256:566927228b0633d3d75550804c52b18a724791e2e2ed64ea7e64e41066663642"
)
SOURCE_REPOSITORY = "rg.fr-par.scw.cloud/mastrao-staging/meet-backend"
TARGET_REPOSITORY = "rg.fr-par.scw.cloud/mastrao-production/meet-backend"

CANDIDATE = Candidate(
    role="meet-backend",
    source_commit=SOURCE_COMMIT,
    source_tree=SOURCE_TREE,
    source_archive_sha256=SOURCE_ARCHIVE_SHA256,
    run_id=RUN_ID,
    run_attempt=RUN_ATTEMPT,
    root_digest=ROOT_DIGEST,
    amd64_manifest_digest=RUNTIME_MANIFEST_DIGEST,
)
ARTIFACT_NAME = CANDIDATE.artifact_name


def github_outputs() -> str:
    """Return the immutable acquisition contract as GitHub step outputs."""
    return "".join(
        (
            f"source_sha={SOURCE_COMMIT}\n",
            f"run_id={RUN_ID}\n",
            f"run_attempt={RUN_ATTEMPT}\n",
            f"artifact_id={ARTIFACT_ID}\n",
            f"artifact_name={ARTIFACT_NAME}\n",
            f"root_digest={ROOT_DIGEST}\n",
            f"source_reference={SOURCE_REPOSITORY}@{ROOT_DIGEST}\n",
            f"target_reference={TARGET_REPOSITORY}@{ROOT_DIGEST}\n",
        )
    )


def verify_artifact_metadata(metadata_path: Path) -> None:
    """Require the one historical artifact selected by this contract."""
    metadata = _read_json(metadata_path)
    workflow_run = metadata.get("workflow_run") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("id") != ARTIFACT_ID
        or metadata.get("name") != ARTIFACT_NAME
        or metadata.get("expired") is not False
        or not isinstance(workflow_run, dict)
        or workflow_run.get("id") != RUN_ID
    ):
        raise ValueError("Actions artifact does not match worker-general selection")


def verify_candidate_receipt(receipt_path: Path) -> None:
    """Require the original immutable candidate receipt byte-for-contract."""
    if _read_json(receipt_path) != expected_receipt(CANDIDATE):
        raise ValueError("candidate receipt does not match worker-general selection")


def render_selection_receipt(
    *,
    candidate_receipt_path: Path,
    graph_path: Path,
    scan_provenance_path: Path,
) -> dict[str, object]:
    """Bind fresh scan evidence to worker-general and no other workload."""
    verify_candidate_receipt(candidate_receipt_path)
    graph = _read_json(graph_path)
    scan = _read_json(scan_provenance_path)
    _validate_graph(graph)
    _validate_scan(scan)

    return {
        "schemaVersion": 1,
        "kind": "mastrao.meet.worker-general-production-selection.v1",
        "status": "SELECTED_SCANNED_NOT_PUBLISHED_NOT_DEPLOYED",
        "source": {
            "repository": "mvirassamy/mastrao-meet",
            "sha": SOURCE_COMMIT,
            "tree": SOURCE_TREE,
            "archiveSha256": SOURCE_ARCHIVE_SHA256,
        },
        "candidate": {
            "sourceRepository": SOURCE_REPOSITORY,
            "sourceReference": f"{SOURCE_REPOSITORY}@{ROOT_DIGEST}",
            "rootDigest": ROOT_DIGEST,
            "runtimeManifestDigest": RUNTIME_MANIFEST_DIGEST,
            "workflow": {
                "runId": RUN_ID,
                "runAttempt": RUN_ATTEMPT,
                "artifactId": ARTIFACT_ID,
                "artifactName": ARTIFACT_NAME,
            },
            "receiptSha256": _sha256(candidate_receipt_path.read_bytes()),
        },
        "target": {
            "namespace": "mastrao-production",
            "kind": "Deployment",
            "name": "worker-general",
            "container": "worker",
            "replicas": 0,
            "repository": TARGET_REPOSITORY,
            "reference": f"{TARGET_REPOSITORY}@{ROOT_DIGEST}",
        },
        "freshEvidence": {
            "evidenceValidUntil": scan["evidenceValidUntil"],
            "ociGraphSha256": _sha256(graph_path.read_bytes()),
            "scanProvenanceSha256": _sha256(scan_provenance_path.read_bytes()),
            "scanStatus": "PASS",
        },
        "rebuildPerformed": False,
        "publicationPerformed": False,
        "deploymentApplied": False,
        "activationPerformed": False,
        "dnsChanged": False,
        "tlsChanged": False,
    }


def _validate_graph(graph: object) -> None:
    manifest_digests = (
        graph.get("ociGraphManifestDigests", []) if isinstance(graph, dict) else []
    )
    if (
        not isinstance(graph, dict)
        or graph.get("kind") != "mastrao.meet.room-audio-oci-graph.v1"
        or graph.get("role") != CANDIDATE.role
        or graph.get("rootDigest") != ROOT_DIGEST
        or RUNTIME_MANIFEST_DIGEST not in manifest_digests
    ):
        raise ValueError("OCI graph does not match worker-general candidate")


def _validate_scan(scan: object) -> None:
    scan_result = scan.get("scan") if isinstance(scan, dict) else None
    provenance = scan.get("provenance") if isinstance(scan, dict) else None
    if not isinstance(scan_result, dict) or not isinstance(provenance, dict):
        raise TypeError("scan provenance does not match worker-general candidate")
    findings = scan_result.get("findings")
    valid = (
        scan.get("kind") == "room-audio-scan-provenance-v1"
        and scan.get("role") == CANDIDATE.role
        and scan.get("rootDigest") == ROOT_DIGEST
        and scan_result.get("status") == "PASS"
        and findings == {"critical": 0, "high": 0, "fixableHighCritical": 0}
        and provenance.get("status") == "PASS"
        and provenance.get("sourceCommit") == SOURCE_COMMIT
        and provenance.get("sourceTree") == SOURCE_TREE
        and provenance.get("sourceArchiveSha256") == SOURCE_ARCHIVE_SHA256
        and isinstance(scan.get("evidenceValidUntil"), str)
        and scan.get("publicationPerformed") is False
        and scan.get("deploymentApplied") is False
    )
    if not valid:
        raise ValueError("scan provenance does not match worker-general candidate")


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.name} is unreadable") from error


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("scan time is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("scan time must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("select")
    artifact = subparsers.add_parser("verify-artifact")
    artifact.add_argument("--metadata", required=True, type=Path)
    receipt = subparsers.add_parser("verify-receipt")
    receipt.add_argument("--receipt", required=True, type=Path)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--receipt", required=True, type=Path)
    inspect.add_argument("--layout", required=True, type=Path)
    inspect.add_argument("--scan-layout", required=True, type=Path)
    inspect.add_argument("--output", required=True, type=Path)

    scan = subparsers.add_parser("render-scan")
    scan.add_argument("--receipt", required=True, type=Path)
    scan.add_argument("--graph", required=True, type=Path)
    scan.add_argument("--trivy-report", required=True, type=Path)
    scan.add_argument("--trivy-version", required=True, type=Path)
    scan.add_argument("--trivy-database", required=True, type=Path)
    scan.add_argument("--trivy-binary", required=True, type=Path)
    scan.add_argument("--scanned-at", required=True)
    scan.add_argument("--output", required=True, type=Path)

    render = subparsers.add_parser("render")
    render.add_argument("--candidate-receipt", required=True, type=Path)
    render.add_argument("--graph", required=True, type=Path)
    render.add_argument("--scan-provenance", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "select":
        sys.stdout.write(github_outputs())
        return
    if args.command == "verify-artifact":
        verify_artifact_metadata(args.metadata)
        return
    if args.command == "verify-receipt":
        verify_candidate_receipt(args.receipt)
        return
    if args.command == "inspect":
        verify_candidate_receipt(args.receipt)
        _write_json(
            args.output,
            inspect_candidate(
                candidate=CANDIDATE,
                receipt_path=args.receipt,
                layout=args.layout,
                scan_layout=args.scan_layout,
            ),
        )
        return
    if args.command == "render-scan":
        verify_candidate_receipt(args.receipt)
        _write_json(
            args.output,
            render_evidence(
                candidate=CANDIDATE,
                graph_path=args.graph,
                trivy_report_path=args.trivy_report,
                trivy_version_path=args.trivy_version,
                trivy_database_path=args.trivy_database,
                trivy_binary_path=args.trivy_binary,
                scanned_at=_parse_time(args.scanned_at),
            ),
        )
        return
    _write_json(
        args.output,
        render_selection_receipt(
            candidate_receipt_path=args.candidate_receipt,
            graph_path=args.graph,
            scan_provenance_path=args.scan_provenance,
        ),
    )


if __name__ == "__main__":
    main()
