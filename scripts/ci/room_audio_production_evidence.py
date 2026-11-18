#!/usr/bin/env python3
"""Produce fail-closed Room Audio production evidence from exact OCI bytes."""

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
from urllib.parse import urlsplit

from scripts.ci.staging_candidate_recipe import TARGETS, Recipe

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TRIVY_VERSION = "0.74.0"
TRIVY_BINARY_DIGEST = (
    "sha256:d89bcc6510a267f11b773398cbf1be5520ce39f9e8b6633178c4487f05b7d791"
)
EVIDENCE_LIFETIME = dt.timedelta(minutes=60)
DATABASE_LIFETIME = dt.timedelta(minutes=360)
ATTESTATION_TYPE = "attestation-manifest"
OCI_MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
TRIVY_SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
METADATA_COLLECTOR_ROLE = "metadata-collector"


@dataclass(frozen=True)
class Source:
    commit: str
    tree: str
    archive_sha256: str


METADATA_COLLECTOR_SOURCE = Source(
    commit="9077554dbed793e14d0c334b8217e5cbefff7b8b",
    tree="8698e10c19064538df2887904d11932807140193",
    archive_sha256=("bfd0646220cedfff00fce6dcb261eeb651a2ddb4464500afbbc95cfd7f8d26b0"),
)
EVIDENCE_RECIPES = {
    **TARGETS,
    METADATA_COLLECTOR_ROLE: Recipe(
        context="src/agents",
        dockerfile="src/agents/Dockerfile",
        build_target="metadata-collector-production",
        repository="rg.fr-par.scw.cloud/mastrao-staging/meet-agents",
        build_args=(
            "DOCKER_USER=10001:10001",
            "SOURCE_DATE_EPOCH=1794617901",
        ),
    ),
}


@dataclass(frozen=True)
class Candidate:
    role: str
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    run_id: int
    run_attempt: int
    root_digest: str
    amd64_manifest_digest: str

    @property
    def repository(self) -> str:
        return evidence_recipe_for(self.role).repository

    @property
    def reference(self) -> str:
        return f"{self.repository}@{self.root_digest}"

    @property
    def artifact_name(self) -> str:
        return (
            f"meet-{self.role}-{self.source_commit}-run-{self.run_id}"
            f"-attempt-{self.run_attempt}-candidate-receipt"
        )


CANDIDATES = {
    "meet-agents": Candidate(
        role="meet-agents",
        source_commit="9077554dbed793e14d0c334b8217e5cbefff7b8b",
        source_tree="8698e10c19064538df2887904d11932807140193",
        source_archive_sha256=(
            "bfd0646220cedfff00fce6dcb261eeb651a2ddb4464500afbbc95cfd7f8d26b0"
        ),
        run_id=43537638339,
        run_attempt=1,
        root_digest=(
            "sha256:efff5c4a75893dccaae902b1fe6d29ebde487479892bd1ff68ca9c6ad5ad999b"
        ),
        amd64_manifest_digest=(
            "sha256:00b91e1e2cb47f111855342a6e7d97bc496d46bfab32fd742eaaee3d50cf0c09"
        ),
    ),
    "meet-backend": Candidate(
        role="meet-backend",
        source_commit="1f19f4c25206f6398164436b601277e3a2fe8f3a",
        source_tree="5a3d060c82feb3f6c39c0f4557c003af2d44be2a",
        source_archive_sha256=(
            "de8f910acb82d434bbf96aa052d03ddba855b05d29501d843f784d72c98a6ac6"
        ),
        run_id=43497052567,
        run_attempt=3,
        root_digest=(
            "sha256:8b4bfa35db20817c07ed3eeed23d218f44005d3da0192eac3ae039eb91e91665"
        ),
        amd64_manifest_digest=(
            "sha256:38f2b842f4dec91c8420331ea1898c73df497bfa6a4478be8629037881c0fc39"
        ),
    ),
}


def evidence_recipe_for(role: str) -> Recipe:
    try:
        return EVIDENCE_RECIPES[role]
    except KeyError as error:
        raise ValueError("unsupported Room Audio evidence role") from error


def candidate_for(role: str) -> Candidate:
    try:
        return CANDIDATES[role]
    except KeyError as error:
        raise ValueError("unsupported Room Audio candidate role") from error


def expected_receipt(candidate: Candidate) -> dict[str, object]:
    if candidate.role == METADATA_COLLECTOR_ROLE:
        return _expected_local_build_receipt(candidate)
    recipe = evidence_recipe_for(candidate.role)
    return {
        "deploymentApplied": False,
        "image": {
            "digest": candidate.root_digest,
            "reference": candidate.reference,
            "repository": candidate.repository,
        },
        "recipe": {
            "buildArgs": list(recipe.build_args),
            "buildTarget": recipe.build_target,
            "context": recipe.context,
            "dockerfile": recipe.dockerfile,
            "platform": "linux/amd64",
        },
        "registryReadback": {
            "digest": candidate.root_digest,
            "imageDigest": candidate.amd64_manifest_digest,
            "platform": "linux/amd64",
            "repository": candidate.repository,
            "schema": "mastrao.meet.staging-candidate-readback.v1",
            "status": "VERIFIED",
        },
        "schema": "mastrao.meet.staging-candidate-receipt.v1",
        "source": {
            "archiveSha256": candidate.source_archive_sha256,
            "repository": "mvirassamy/mastrao-meet",
            "sha": candidate.source_commit,
            "tree": candidate.source_tree,
        },
        "status": "PUBLISHED_NOT_DEPLOYED",
        "target": candidate.role,
        "workflow": {
            "runAttempt": candidate.run_attempt,
            "runId": candidate.run_id,
        },
    }


def _expected_local_build_receipt(candidate: Candidate) -> dict[str, object]:
    recipe = evidence_recipe_for(candidate.role)
    return {
        "schemaVersion": 1,
        "kind": "mastrao.meet.production-evidence-build-receipt.v1",
        "role": candidate.role,
        "status": "BUILT_NOT_PUBLISHED_NOT_DEPLOYED",
        "source": {
            "repository": "mvirassamy/mastrao-meet",
            "sha": candidate.source_commit,
            "tree": candidate.source_tree,
            "archiveSha256": candidate.source_archive_sha256,
        },
        "recipe": {
            "context": recipe.context,
            "dockerfile": recipe.dockerfile,
            "buildTarget": recipe.build_target,
            "buildArgs": list(recipe.build_args),
            "platform": "linux/amd64",
        },
        "image": {
            "repository": candidate.repository,
            "rootDigest": candidate.root_digest,
            "runtimeManifestDigest": candidate.amd64_manifest_digest,
        },
        "workflow": {
            "runId": candidate.run_id,
            "runAttempt": candidate.run_attempt,
        },
        "publicationPerformed": False,
        "deploymentApplied": False,
    }


def candidate_from_receipt(role: str, receipt_path: Path) -> Candidate:
    if role != METADATA_COLLECTOR_ROLE:
        candidate = candidate_for(role)
        if _read_json(receipt_path) != expected_receipt(candidate):
            raise ValueError("candidate receipt does not match the closed candidate")
        return candidate

    receipt = _read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise ValueError("local build receipt is malformed")
    image = receipt.get("image")
    workflow = receipt.get("workflow")
    if not isinstance(image, dict) or not isinstance(workflow, dict):
        raise ValueError("local build receipt is malformed")
    root_digest = image.get("rootDigest")
    runtime_digest = image.get("runtimeManifestDigest")
    run_id = workflow.get("runId")
    run_attempt = workflow.get("runAttempt")
    if (
        not _is_digest(root_digest)
        or not _is_digest(runtime_digest)
        or not isinstance(run_id, int)
        or run_id < 1
        or not isinstance(run_attempt, int)
        or run_attempt < 1
    ):
        raise ValueError("local build receipt is malformed")
    candidate = Candidate(
        role=METADATA_COLLECTOR_ROLE,
        source_commit=METADATA_COLLECTOR_SOURCE.commit,
        source_tree=METADATA_COLLECTOR_SOURCE.tree,
        source_archive_sha256=METADATA_COLLECTOR_SOURCE.archive_sha256,
        run_id=run_id,
        run_attempt=run_attempt,
        root_digest=root_digest,
        amd64_manifest_digest=runtime_digest,
    )
    if receipt != expected_receipt(candidate):
        raise ValueError("local build receipt does not match the closed build")
    return candidate


def render_local_build_receipt(
    *,
    layout: Path,
    expected_root_digest: str,
    run_id: int,
    run_attempt: int,
) -> dict[str, object]:
    if not _is_digest(expected_root_digest):
        raise ValueError("local build root digest is invalid")
    if run_id < 1 or run_attempt < 1:
        raise ValueError("local build workflow identity is invalid")
    index = _read_json(layout / "index.json")
    roots = _manifest_descriptors(index, "OCI layout index")
    if len(roots) != 1 or roots[0]["digest"] != expected_root_digest:
        raise ValueError("OCI layout does not match the local build root digest")
    root = _read_blob(layout, expected_root_digest, roots[0]["size"])
    runtime_descriptors = [
        descriptor
        for descriptor in _manifest_descriptors(root, "candidate root")
        if not _is_attestation(descriptor)
    ]
    if len(runtime_descriptors) != 1:
        raise ValueError("local build must contain exactly one runtime manifest")
    runtime = runtime_descriptors[0]
    candidate = Candidate(
        role=METADATA_COLLECTOR_ROLE,
        source_commit=METADATA_COLLECTOR_SOURCE.commit,
        source_tree=METADATA_COLLECTOR_SOURCE.tree,
        source_archive_sha256=METADATA_COLLECTOR_SOURCE.archive_sha256,
        run_id=run_id,
        run_attempt=run_attempt,
        root_digest=expected_root_digest,
        amd64_manifest_digest=runtime["digest"],
    )
    _inspect_graph(candidate, layout)
    return expected_receipt(candidate)


def inspect_candidate(
    *, candidate: Candidate, receipt_path: Path, layout: Path, scan_layout: Path
) -> dict[str, object]:
    receipt = _read_json(receipt_path)
    if receipt != expected_receipt(candidate):
        raise ValueError("candidate receipt does not match the closed candidate")
    graph, runtime = _inspect_graph(candidate, layout)
    _prepare_scan_layout(layout, scan_layout, runtime)
    return graph


def _inspect_graph(
    candidate: Candidate, layout: Path
) -> tuple[dict[str, object], dict[str, object]]:
    root = _read_blob(layout, candidate.root_digest)
    descriptors = _manifest_descriptors(root, "candidate root")
    manifests = []
    runtime = None
    provenance_records = []
    for descriptor in descriptors:
        manifest = _read_blob(layout, descriptor["digest"], descriptor["size"])
        config = _read_descriptor_blob(layout, manifest.get("config"), "config")
        layers = [
            _read_descriptor_blob(layout, layer, "layer")
            for layer in _descriptor_list(manifest.get("layers"), "layers")
        ]
        manifests.append(
            {
                "annotations": descriptor.get("annotations", {}),
                "config": config,
                "digest": descriptor["digest"],
                "layers": layers,
                "mediaType": descriptor.get("mediaType"),
                "platform": descriptor.get("platform"),
                "size": descriptor["size"],
            }
        )
        if _is_attestation(descriptor):
            reference = _annotation(descriptor, "vnd.docker.reference.digest")
            if reference == candidate.amd64_manifest_digest:
                provenance_records.append(
                    _validate_buildkit_provenance(
                        layout=layout,
                        descriptor=descriptor,
                        manifest=manifest,
                        candidate=candidate,
                    )
                )
            continue
        if runtime is not None:
            raise ValueError("candidate root contains multiple runtime manifests")
        runtime = (descriptor, config)

    if runtime is None or runtime[0]["digest"] != candidate.amd64_manifest_digest:
        raise ValueError(
            "candidate runtime manifest does not match the closed candidate"
        )
    platform = runtime[0].get("platform")
    if not isinstance(platform, dict) or (
        platform.get("os"),
        platform.get("architecture"),
    ) != ("linux", "amd64"):
        raise ValueError("candidate runtime platform is not linux/amd64")
    if len(provenance_records) != 1:
        raise ValueError(
            "candidate must have exactly one validated BuildKit provenance statement"
        )

    graph = {
        "schemaVersion": 1,
        "kind": "mastrao.meet.room-audio-oci-graph.v1",
        "role": candidate.role,
        "rootDigest": candidate.root_digest,
        "configDigest": runtime[1]["digest"],
        "ociGraphManifestDigests": [item["digest"] for item in descriptors],
        "manifests": manifests,
        "provenance": provenance_records[0],
    }
    return graph, runtime[0]


def render_evidence(
    *,
    candidate: Candidate,
    graph_path: Path,
    trivy_report_path: Path,
    trivy_version_path: Path,
    trivy_database_path: Path,
    trivy_binary_path: Path,
    scanned_at: dt.datetime,
) -> dict[str, object]:
    graph = _read_json(graph_path)
    _validate_graph_identity(graph, candidate)
    report = _read_json(trivy_report_path)
    version = _read_json(trivy_version_path)

    try:
        trivy_binary_digest = _sha256(trivy_binary_path.read_bytes())
    except OSError as error:
        raise ValueError("Trivy binary is unreadable") from error
    if trivy_binary_digest != TRIVY_BINARY_DIGEST:
        raise ValueError("Trivy binary digest does not match the production policy")

    if version.get("Version") != TRIVY_VERSION:
        raise ValueError("Trivy version does not match the production policy")
    database = version.get("VulnerabilityDB")
    if not isinstance(database, dict):
        raise ValueError("Trivy vulnerability database metadata is missing")
    database_updated_at = _parse_time(database.get("UpdatedAt"), "database UpdatedAt")
    if database_updated_at > scanned_at:
        raise ValueError("Trivy vulnerability database is newer than the scan")
    if scanned_at - database_updated_at > DATABASE_LIFETIME:
        raise ValueError("Trivy vulnerability database is stale")

    metadata = report.get("Metadata")
    if not isinstance(metadata, dict) or metadata.get("ImageID") != graph["configDigest"]:
        raise ValueError("Trivy report is not bound to the candidate config digest")
    findings = _count_findings(report)
    if findings != {"critical": 0, "high": 0, "fixableHighCritical": 0}:
        raise ValueError("Trivy report contains high or critical findings")

    evidence_valid_until = min(
        scanned_at + EVIDENCE_LIFETIME,
        database_updated_at + DATABASE_LIFETIME,
    )
    return {
        "schemaVersion": 1,
        "kind": "room-audio-scan-provenance-v1",
        "role": candidate.role,
        "rootDigest": candidate.root_digest,
        "configDigest": graph["configDigest"],
        "ociGraph": {
            "kind": graph["kind"],
            "manifestDigests": graph["ociGraphManifestDigests"],
            "sha256": _sha256(graph_path.read_bytes()),
        },
        "scan": {
            "status": "PASS",
            "findings": findings,
            "scanner": {
                "name": "Trivy",
                "version": TRIVY_VERSION,
            },
            "policy": {
                "ignoreUnfixed": True,
                "severities": ["HIGH", "CRITICAL"],
            },
            "vulnerabilityDatabase": {
                "source": "aquasecurity/trivy-db",
                "digest": _sha256(trivy_database_path.read_bytes()),
                "updatedAt": _format_time(database_updated_at),
            },
            "scannedAt": _format_time(scanned_at),
        },
        "evidenceValidUntil": _format_time(evidence_valid_until),
        "provenance": {
            "status": "PASS",
            "sourceCommit": candidate.source_commit,
            "sourceTree": candidate.source_tree,
            "sourceArchiveSha256": candidate.source_archive_sha256,
        },
        "scannerBinaryDigest": trivy_binary_digest,
        "slsaProvenance": {
            "attestationManifestDigest": graph["provenance"][
                "attestationManifestDigest"
            ],
            "statementDigest": graph["provenance"]["statementDigest"],
            "predicateType": graph["provenance"]["predicateType"],
            "subjectDigest": graph["provenance"]["subjectDigest"],
        },
        "publicationPerformed": False,
        "deploymentApplied": False,
    }


def _validate_graph_identity(graph: object, candidate: Candidate) -> None:
    if not isinstance(graph, dict):
        raise ValueError("OCI graph is malformed")
    if (
        graph.get("kind") != "mastrao.meet.room-audio-oci-graph.v1"
        or graph.get("role") != candidate.role
        or graph.get("rootDigest") != candidate.root_digest
    ):
        raise ValueError("OCI graph does not match the closed candidate")
    manifest_digests = graph.get("ociGraphManifestDigests")
    if (
        not isinstance(manifest_digests, list)
        or candidate.amd64_manifest_digest not in manifest_digests
        or len(manifest_digests) != len(set(manifest_digests))
        or not all(isinstance(item, str) and DIGEST.fullmatch(item) for item in manifest_digests)
    ):
        raise ValueError("OCI graph manifest list is incomplete")
    config_digest = graph.get("configDigest")
    if not isinstance(config_digest, str) or not DIGEST.fullmatch(config_digest):
        raise ValueError("OCI graph config digest is invalid")
    provenance = graph.get("provenance")
    if not isinstance(provenance, dict) or (
        provenance.get("status") != "VALIDATED"
        or provenance.get("predicateType") != SLSA_PROVENANCE_TYPE
        or provenance.get("subjectDigest") != candidate.amd64_manifest_digest
        or provenance.get("attestationManifestDigest") not in manifest_digests
        or not _is_digest(provenance.get("statementDigest"))
    ):
        raise ValueError("OCI graph provenance is not validated")


def _count_findings(report: object) -> dict[str, int]:
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("Results"), list)
        or not report["Results"]
    ):
        raise ValueError("Trivy report is malformed")
    critical = 0
    high = 0
    fixable = 0
    for result in report["Results"]:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("Target"), str)
            or not result["Target"]
        ):
            raise ValueError("Trivy result is malformed")
        vulnerabilities = result.get("Vulnerabilities")
        if vulnerabilities is None:
            continue
        if not isinstance(vulnerabilities, list):
            raise ValueError("Trivy vulnerabilities are malformed")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise ValueError("Trivy vulnerability is malformed")
            severity = vulnerability.get("Severity")
            if not isinstance(severity, str) or severity not in TRIVY_SEVERITIES:
                raise ValueError("Trivy vulnerability severity is malformed")
            if severity == "CRITICAL":
                critical += 1
            elif severity == "HIGH":
                high += 1
            else:
                continue
            if vulnerability.get("FixedVersion"):
                fixable += 1
    return {"critical": critical, "high": high, "fixableHighCritical": fixable}


def _prepare_scan_layout(source: Path, destination: Path, runtime: dict[str, object]) -> None:
    if destination.exists():
        raise ValueError("scan layout destination already exists")
    shutil.copytree(source, destination)
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [runtime],
    }
    (destination / "index.json").write_text(
        json.dumps(index, separators=(",", ":")) + "\n"
    )


def _manifest_descriptors(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        raise ValueError(f"{label} is not an OCI v2 manifest")
    return _descriptor_list(value.get("manifests"), f"{label} manifests")


def _descriptor_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} are missing")
    descriptors = []
    for descriptor in value:
        if (
            not isinstance(descriptor, dict)
            or not isinstance(descriptor.get("digest"), str)
            or not DIGEST.fullmatch(descriptor["digest"])
            or not isinstance(descriptor.get("size"), int)
            or descriptor["size"] < 0
        ):
            raise ValueError(f"{label} contain an invalid descriptor")
        descriptors.append(descriptor)
    return descriptors


def _read_descriptor_blob(layout: Path, descriptor: object, label: str) -> dict[str, object]:
    if not isinstance(descriptor, dict):
        raise ValueError(f"OCI {label} descriptor is missing")
    descriptors = _descriptor_list([descriptor], f"OCI {label} descriptor")
    _read_blob_bytes(layout, descriptors[0]["digest"], descriptors[0]["size"])
    return {
        "digest": descriptors[0]["digest"],
        "mediaType": descriptors[0].get("mediaType"),
        "size": descriptors[0]["size"],
    }


def _validate_buildkit_provenance(
    *,
    layout: Path,
    descriptor: dict[str, object],
    manifest: dict[str, object],
    candidate: Candidate,
) -> dict[str, object]:
    if (
        descriptor.get("mediaType") != OCI_MANIFEST_TYPE
        or manifest.get("mediaType") != OCI_MANIFEST_TYPE
    ):
        raise ValueError("BuildKit attestation manifest media type is invalid")

    statements = []
    in_toto_layers = 0
    for layer in _descriptor_list(manifest.get("layers"), "attestation layers"):
        if layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
            continue
        in_toto_layers += 1
        raw = _read_blob_bytes(layout, layer["digest"], layer["size"])
        statement = _parse_json_object(raw, "BuildKit in-toto statement")
        if statement.get("predicateType") == SLSA_PROVENANCE_TYPE:
            statements.append((statement, _sha256(raw)))

    if in_toto_layers == 0:
        raise ValueError("BuildKit attestation layer media type is invalid")
    if len(statements) != 1:
        raise ValueError(
            "BuildKit attestation must contain exactly one SLSA provenance statement"
        )
    statement, statement_digest = statements[0]
    _validate_provenance_statement(statement, candidate)
    return {
        "status": "VALIDATED",
        "attestationManifestDigest": descriptor["digest"],
        "statementDigest": statement_digest,
        "predicateType": SLSA_PROVENANCE_TYPE,
        "subjectDigest": candidate.amd64_manifest_digest,
    }


def _validate_provenance_statement(
    statement: dict[str, object], candidate: Candidate
) -> None:
    if statement.get("_type") != IN_TOTO_STATEMENT_TYPE:
        raise ValueError("BuildKit in-toto statement type is invalid")
    if statement.get("predicateType") != SLSA_PROVENANCE_TYPE:
        raise ValueError("BuildKit provenance predicate type is invalid")

    subjects = statement.get("subject")
    expected_subject = candidate.amd64_manifest_digest.removeprefix("sha256:")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("BuildKit provenance subject is invalid")
    subject = subjects[0]
    if not isinstance(subject, dict) or not _is_nonempty_string(subject.get("name")):
        raise ValueError("BuildKit provenance subject is invalid")
    subject_digest = subject.get("digest")
    if not _is_valid_digest_set(subject_digest):
        raise ValueError("BuildKit provenance subject is invalid")
    if subject_digest.get("sha256") != expected_subject:
        raise ValueError("BuildKit provenance subject does not match the runtime image")

    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise ValueError("BuildKit provenance predicate is invalid")
    build_definition = predicate.get("buildDefinition")
    run_details = predicate.get("runDetails")
    if not isinstance(build_definition, dict) or not isinstance(run_details, dict):
        raise ValueError("BuildKit SLSA provenance predicate is invalid")
    _validate_slsa_structure(build_definition, run_details)
    _validate_optional_build_parameters(build_definition, candidate)


def _validate_slsa_structure(
    build_definition: dict[str, object], run_details: dict[str, object]
) -> None:
    if not _is_nonempty_uri(build_definition.get("buildType")):
        raise ValueError("BuildKit SLSA provenance structure is invalid")
    if not isinstance(build_definition.get("externalParameters"), dict):
        raise ValueError("BuildKit SLSA provenance structure is invalid")

    if "internalParameters" in build_definition and not isinstance(
        build_definition["internalParameters"], dict
    ):
        raise ValueError("BuildKit SLSA provenance structure is invalid")
    if "resolvedDependencies" in build_definition:
        _validate_resource_descriptor_list(
            build_definition["resolvedDependencies"],
            "BuildKit SLSA provenance resolved dependencies",
        )

    builder = run_details.get("builder")
    if not isinstance(builder, dict) or not _is_nonempty_uri(builder.get("id")):
        raise ValueError("BuildKit SLSA provenance structure is invalid")
    if "metadata" in run_details and not isinstance(run_details["metadata"], dict):
        raise ValueError("BuildKit SLSA provenance structure is invalid")


def _validate_resource_descriptor_list(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(
        _is_valid_resource_descriptor(item) for item in value
    ):
        raise ValueError(f"{label} are invalid")


def _is_valid_resource_descriptor(value: object) -> bool:
    if not isinstance(value, dict):
        return False

    uri = value.get("uri")
    digest_set = value.get("digest")
    content = value.get("content")
    if uri is None and digest_set is None and content is None:
        return False
    if uri is not None and not _is_nonempty_uri(uri):
        return False
    if digest_set is not None and not _is_valid_digest_set(digest_set):
        return False
    if content is not None and not _is_nonempty_string(content):
        return False

    if "name" in value and not _is_nonempty_string(value["name"]):
        return False
    if "downloadLocation" in value and not _is_nonempty_uri(value["downloadLocation"]):
        return False
    if "mediaType" in value and not _is_nonempty_string(value["mediaType"]):
        return False
    if "annotations" in value and not isinstance(value["annotations"], dict):
        return False
    return True


def _is_valid_digest_set(value: object) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            _is_nonempty_string(algorithm) and _is_nonempty_string(digest_value)
            for algorithm, digest_value in value.items()
        )
    )


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_nonempty_uri(value: object) -> bool:
    if not _is_nonempty_string(value) or any(
        character.isspace() for character in value
    ):
        return False
    try:
        return bool(urlsplit(value).scheme)
    except ValueError:
        return False


def _validate_optional_build_parameters(
    build_definition: dict[str, object], candidate: Candidate
) -> None:
    external = build_definition.get("externalParameters")
    if not isinstance(external, dict):
        raise ValueError("BuildKit SLSA provenance structure is invalid")

    recipe = evidence_recipe_for(candidate.role)
    config_source = external.get("configSource")
    if config_source is not None:
        expected_dockerfile = Path(recipe.dockerfile)
        if recipe.context != ".":
            expected_dockerfile = expected_dockerfile.relative_to(recipe.context)
        if not isinstance(config_source, dict) or (
            config_source.get("path") != expected_dockerfile.as_posix()
        ):
            raise ValueError("BuildKit provenance build recipe does not match")

    argument_maps = _build_argument_maps(external)
    for arguments in argument_maps:
        _validate_source_commit(arguments, candidate.source_commit)
        if "target" in arguments and arguments["target"] != recipe.build_target:
            raise ValueError("BuildKit provenance build recipe does not match")
        for build_arg in recipe.build_args:
            name, expected_value = build_arg.split("=", 1)
            key = f"build-arg:{name}"
            if key in arguments and arguments[key] != expected_value:
                raise ValueError("BuildKit provenance build recipe does not match")
    if candidate.role == METADATA_COLLECTOR_ROLE:
        _require_metadata_collector_build_parameters(
            config_source=config_source,
            argument_maps=argument_maps,
            recipe=recipe,
        )


def _require_metadata_collector_build_parameters(
    *,
    config_source: object,
    argument_maps: list[dict[str, object]],
    recipe: Recipe,
) -> None:
    if config_source is None or not argument_maps:
        raise ValueError("metadata collector BuildKit recipe is incomplete")
    for arguments in argument_maps:
        if arguments.get("target") != recipe.build_target:
            continue
        if all(
            arguments.get(f"build-arg:{name}") == expected_value
            for name, expected_value in (
                build_arg.split("=", 1) for build_arg in recipe.build_args
            )
        ):
            return
    raise ValueError("metadata collector BuildKit recipe is incomplete")


def _build_argument_maps(external: dict[str, object]) -> list[dict[str, object]]:
    maps = []
    for request in (external.get("request"), _nested_request(external.get("root"))):
        if request is None:
            continue
        if not isinstance(request, dict):
            raise ValueError("BuildKit provenance build recipe is malformed")
        arguments = request.get("args")
        if arguments is None:
            continue
        if not isinstance(arguments, dict):
            raise ValueError("BuildKit provenance build recipe is malformed")
        maps.append(arguments)
    return maps


def _nested_request(root: object) -> object:
    if root is None:
        return None
    if not isinstance(root, dict):
        raise ValueError("BuildKit provenance build recipe is malformed")
    return root.get("request")


def _validate_source_commit(arguments: dict[str, object], expected: str) -> None:
    for key in (
        "vcs:revision",
        "label:revision",
        "label:org.opencontainers.image.revision",
    ):
        if key in arguments and arguments[key] != expected:
            raise ValueError("BuildKit provenance source commit does not match")


def _parse_json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _read_blob(layout: Path, digest: str, expected_size: int | None = None) -> dict[str, object]:
    raw = _read_blob_bytes(layout, digest, expected_size)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("OCI manifest blob is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("OCI manifest blob is not an object")
    return value


def _read_blob_bytes(layout: Path, digest: str, expected_size: int | None = None) -> bytes:
    if not DIGEST.fullmatch(digest):
        raise ValueError("OCI blob digest is invalid")
    raw = (layout / "blobs" / "sha256" / digest.removeprefix("sha256:")).read_bytes()
    if _sha256(raw) != digest:
        raise ValueError("OCI blob bytes do not match their digest")
    if expected_size is not None and len(raw) != expected_size:
        raise ValueError("OCI blob bytes do not match their descriptor size")
    return raw


def _annotation(descriptor: dict[str, object], name: str) -> object:
    annotations = descriptor.get("annotations")
    return annotations.get(name) if isinstance(annotations, dict) else None


def _is_attestation(descriptor: dict[str, object]) -> bool:
    return _annotation(descriptor, "vnd.docker.reference.type") == ATTESTATION_TYPE


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.name} is unreadable") from error


def _parse_time(value: object, label: str) -> dt.datetime:
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


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _select(role: str) -> str:
    if role == METADATA_COLLECTOR_ROLE:
        recipe = evidence_recipe_for(role)
        return "".join(
            (
                "acquisition=local-build\n",
                f"role={role}\n",
                f"source_sha={METADATA_COLLECTOR_SOURCE.commit}\n",
                f"source_tree={METADATA_COLLECTOR_SOURCE.tree}\n",
                f"source_archive_sha256={METADATA_COLLECTOR_SOURCE.archive_sha256}\n",
                f"context={recipe.context}\n",
                f"dockerfile={recipe.dockerfile}\n",
                f"build_target={recipe.build_target}\n",
                f"repository={recipe.repository}\n",
                "build_args<<METADATA_COLLECTOR_BUILD_ARGS\n",
                *(f"{build_arg}\n" for build_arg in recipe.build_args),
                "METADATA_COLLECTOR_BUILD_ARGS\n",
            )
        )
    candidate = candidate_for(role)
    return "".join(
        (
            "acquisition=registry\n",
            f"role={candidate.role}\n",
            f"source_sha={candidate.source_commit}\n",
            f"run_id={candidate.run_id}\n",
            f"run_attempt={candidate.run_attempt}\n",
            f"root_digest={candidate.root_digest}\n",
            f"source_reference={candidate.reference}\n",
            f"artifact_name={candidate.artifact_name}\n",
        )
    )


def artifact_id_from_listing(path: Path, expected_name: str) -> str:
    listing = _read_json(path)
    artifacts = listing.get("artifacts") if isinstance(listing, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("Actions artifact listing is malformed")
    matches = [
        str(item["id"])
        for item in artifacts
        if isinstance(item, dict)
        and item.get("name") == expected_name
        and item.get("expired") is False
        and isinstance(item.get("id"), int)
    ]
    if len(matches) != 1:
        raise ValueError("expected exactly one unexpired candidate receipt artifact")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--role", required=True)

    artifact = subparsers.add_parser("artifact-id")
    artifact.add_argument("--listing", required=True, type=Path)
    artifact.add_argument("--name", required=True)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--role", required=True)
    inspect.add_argument("--receipt", required=True, type=Path)
    inspect.add_argument("--layout", required=True, type=Path)
    inspect.add_argument("--scan-layout", required=True, type=Path)
    inspect.add_argument("--output", required=True, type=Path)

    local_receipt = subparsers.add_parser("render-local-build-receipt")
    local_receipt.add_argument("--role", required=True)
    local_receipt.add_argument("--layout", required=True, type=Path)
    local_receipt.add_argument("--expected-root-digest", required=True)
    local_receipt.add_argument("--run-id", required=True, type=int)
    local_receipt.add_argument("--run-attempt", required=True, type=int)
    local_receipt.add_argument("--output", required=True, type=Path)

    render = subparsers.add_parser("render")
    render.add_argument("--role", required=True)
    render.add_argument("--receipt", required=True, type=Path)
    render.add_argument("--graph", required=True, type=Path)
    render.add_argument("--trivy-report", required=True, type=Path)
    render.add_argument("--trivy-version", required=True, type=Path)
    render.add_argument("--trivy-database", required=True, type=Path)
    render.add_argument("--trivy-binary", required=True, type=Path)
    render.add_argument("--scanned-at", required=True)
    render.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "select":
        sys.stdout.write(_select(args.role))
        return
    if args.command == "artifact-id":
        sys.stdout.write(f"{artifact_id_from_listing(args.listing, args.name)}\n")
        return
    if args.command == "render-local-build-receipt":
        if args.role != METADATA_COLLECTOR_ROLE:
            raise ValueError(
                "local build receipt is only supported for metadata-collector"
            )
        _write_json(
            args.output,
            render_local_build_receipt(
                layout=args.layout,
                expected_root_digest=args.expected_root_digest,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            ),
        )
        return
    candidate = candidate_from_receipt(args.role, args.receipt)
    if args.command == "inspect":
        _write_json(
            args.output,
            inspect_candidate(
                candidate=candidate,
                receipt_path=args.receipt,
                layout=args.layout,
                scan_layout=args.scan_layout,
            ),
        )
        return
    _write_json(
        args.output,
        render_evidence(
            candidate=candidate,
            graph_path=args.graph,
            trivy_report_path=args.trivy_report,
            trivy_version_path=args.trivy_version,
            trivy_database_path=args.trivy_database,
            trivy_binary_path=args.trivy_binary,
            scanned_at=_parse_time(args.scanned_at, "scan time"),
        ),
    )


if __name__ == "__main__":
    main()
