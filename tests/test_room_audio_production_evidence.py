import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from runpy import run_path
from unittest import mock

import scripts.ci.room_audio_production_evidence as evidence_module
from scripts.ci.room_audio_production_evidence import (
    CANDIDATES,
    artifact_id_from_listing,
    expected_receipt,
    inspect_candidate,
    render_evidence,
)

MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
CONFIG_TYPE = "application/vnd.oci.image.config.v1+json"
IN_TOTO_TYPE = "application/vnd.in-toto+json"
SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(",", ":")))


def add_blob(layout, value):
    raw = json.dumps(value, separators=(",", ":")).encode()
    value_digest = digest(raw)
    path = layout / "blobs" / "sha256" / value_digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"digest": value_digest, "size": len(raw)}


def provenance_statement(candidate, runtime_digest):
    recipe = evidence_module.TARGETS[candidate.role]
    dockerfile = Path(recipe.dockerfile)
    if recipe.context != ".":
        dockerfile = dockerfile.relative_to(recipe.context)
    request_args = {
        "target": recipe.build_target,
        "vcs:revision": candidate.source_commit,
    }
    for build_arg in recipe.build_args:
        name, value = build_arg.split("=", 1)
        request_args[f"build-arg:{name}"] = value
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": SLSA_PROVENANCE_TYPE,
        "subject": [
            {
                "name": candidate.repository,
                "digest": {"sha256": runtime_digest.removeprefix("sha256:")},
            }
        ],
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/moby/buildkit",
                "externalParameters": {
                    "configSource": {"path": dockerfile.as_posix()},
                    "request": {"args": request_args},
                },
            },
            "runDetails": {"builder": {"id": "https://buildkit.example/builder"}},
        },
    }


def replace_with_digest_matching_structurally_empty_slsa(statement):
    subject_digest = statement["subject"][0]["digest"]
    statement["subject"] = [{"digest": subject_digest}]
    statement["predicate"] = {"buildDefinition": {}, "runDetails": {}}


def add_valid_optional_slsa_fields(statement):
    build_definition = statement["predicate"]["buildDefinition"]
    build_definition["internalParameters"] = {}
    build_definition["resolvedDependencies"] = [
        {
            "uri": "git+https://github.com/mvirassamy/mastrao-meet",
            "digest": {"gitCommit": "a" * 40},
        }
    ]
    statement["predicate"]["runDetails"]["metadata"] = {}


def replace_nested_value(statement, path, value):
    target = statement
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def candidate_layout(
    directory,
    candidate,
    *,
    statement_mutation=None,
    attestation_layer_media_type=IN_TOTO_TYPE,
):
    layout = directory / "layout"
    layout.mkdir()
    write_json(layout / "oci-layout", {"imageLayoutVersion": "1.0.0"})
    config = add_blob(layout, {"architecture": "amd64", "os": "linux"})
    config["mediaType"] = CONFIG_TYPE
    layer = add_blob(layout, {"content": "runtime"})
    layer["mediaType"] = "application/vnd.oci.image.layer.v1.tar+gzip"
    runtime = add_blob(
        layout,
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST_TYPE,
            "config": config,
            "layers": [layer],
        },
    )
    runtime.update(
        {
            "mediaType": MANIFEST_TYPE,
            "platform": {"architecture": "amd64", "os": "linux"},
        }
    )
    attestation_config = add_blob(layout, {"architecture": "unknown", "os": "unknown"})
    attestation_config["mediaType"] = CONFIG_TYPE
    statement = provenance_statement(candidate, runtime["digest"])
    if statement_mutation is not None:
        statement_mutation(statement)
    attestation_layer = add_blob(layout, statement)
    attestation_layer["mediaType"] = attestation_layer_media_type
    attestation = add_blob(
        layout,
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST_TYPE,
            "config": attestation_config,
            "layers": [attestation_layer],
        },
    )
    attestation.update(
        {
            "annotations": {
                "vnd.docker.reference.digest": runtime["digest"],
                "vnd.docker.reference.type": "attestation-manifest",
            },
            "mediaType": MANIFEST_TYPE,
            "platform": {"architecture": "unknown", "os": "unknown"},
        }
    )
    root = add_blob(
        layout,
        {
            "schemaVersion": 2,
            "mediaType": INDEX_TYPE,
            "manifests": [runtime, attestation],
        },
    )
    root.update({"mediaType": INDEX_TYPE})
    write_json(layout / "index.json", {"schemaVersion": 2, "manifests": [root]})
    return layout, runtime, config, root


class RoomAudioProductionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.original = CANDIDATES["meet-agents"]
        self.trivy_binary = b"synthetic trivy binary"
        patcher = mock.patch.object(
            evidence_module,
            "TRIVY_BINARY_DIGEST",
            digest(self.trivy_binary),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def fixture(self, directory, **layout_options):
        layout, runtime, config, root = candidate_layout(
            directory, self.original, **layout_options
        )
        candidate = self.original.__class__(
            **{
                **self.original.__dict__,
                "root_digest": root["digest"],
                "amd64_manifest_digest": runtime["digest"],
            }
        )
        receipt = directory / "receipt.json"
        write_json(receipt, expected_receipt(candidate))
        return candidate, layout, receipt, config

    def trivy_binary_path(self, directory):
        path = directory / "trivy"
        path.write_bytes(self.trivy_binary)
        return path

    def test_artifact_selection_requires_one_current_exact_name(self):
        with tempfile.TemporaryDirectory() as name:
            listing = Path(name) / "artifacts.json"
            write_json(
                listing,
                {
                    "artifacts": [
                        {"id": 11, "name": "expected", "expired": False},
                        {"id": 12, "name": "other", "expired": False},
                        {"id": 13, "name": "expected", "expired": True},
                    ]
                },
            )
            self.assertEqual(artifact_id_from_listing(listing, "expected"), "11")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                artifact_id_from_listing(listing, "missing")

    def test_inspection_binds_receipt_graph_config_and_scan_projection(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, config = self.fixture(directory)
            scan_layout = directory / "scan-layout"
            graph = inspect_candidate(
                candidate=candidate,
                receipt_path=receipt,
                layout=layout,
                scan_layout=scan_layout,
            )
            self.assertEqual(graph["rootDigest"], candidate.root_digest)
            self.assertEqual(graph["configDigest"], config["digest"])
            self.assertIn(candidate.amd64_manifest_digest, graph["ociGraphManifestDigests"])
            self.assertEqual(graph["provenance"]["status"], "VALIDATED")
            self.assertEqual(
                graph["provenance"]["subjectDigest"],
                candidate.amd64_manifest_digest,
            )
            scan_index = json.loads((scan_layout / "index.json").read_text())
            self.assertEqual(
                [item["digest"] for item in scan_index["manifests"]],
                [candidate.amd64_manifest_digest],
            )

    def test_inspection_rejects_receipt_or_blob_drift(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, _ = self.fixture(directory)
            changed = expected_receipt(candidate)
            changed["source"]["tree"] = "f" * 40
            write_json(receipt, changed)
            with self.assertRaisesRegex(ValueError, "closed candidate"):
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout",
                )

            write_json(receipt, expected_receipt(candidate))
            root = layout / "blobs" / "sha256" / candidate.root_digest.removeprefix(
                "sha256:"
            )
            root.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "do not match"):
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout-2",
                )

    def test_inspection_rejects_unvalidated_buildkit_provenance(self):
        cases = (
            (
                "media type",
                {"attestation_layer_media_type": "application/json"},
            ),
            (
                "provenance",
                {
                    "statement_mutation": lambda statement: statement.update(
                        {"predicateType": "https://example.invalid/predicate"}
                    )
                },
            ),
            (
                "subject",
                {
                    "statement_mutation": lambda statement: statement["subject"][0][
                        "digest"
                    ].update({"sha256": "f" * 64})
                },
            ),
            (
                "source commit",
                {
                    "statement_mutation": lambda statement: statement["predicate"][
                        "buildDefinition"
                    ]["externalParameters"]["request"]["args"].update(
                        {"vcs:revision": "f" * 40}
                    )
                },
            ),
            (
                "build recipe",
                {
                    "statement_mutation": lambda statement: statement["predicate"][
                        "buildDefinition"
                    ]["externalParameters"]["request"]["args"].update(
                        {"target": "foreign-target"}
                    )
                },
            ),
        )
        for message, options in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as name:
                directory = Path(name)
                candidate, layout, receipt, _ = self.fixture(directory, **options)
                with self.assertRaisesRegex(ValueError, message):
                    inspect_candidate(
                        candidate=candidate,
                        receipt_path=receipt,
                        layout=layout,
                        scan_layout=directory / "scan-layout",
                    )

    def test_inspection_rejects_digest_matching_structurally_empty_slsa(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, _ = self.fixture(
                directory,
                statement_mutation=(
                    replace_with_digest_matching_structurally_empty_slsa
                ),
            )
            with self.assertRaisesRegex(ValueError, "subject"):
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout",
                )

    def test_inspection_requires_structurally_valid_slsa_v1_fields(self):
        cases = (
            ("subject name", ("subject", 0, "name"), ""),
            ("subject digest", ("subject", 0, "digest"), {"sha256": 7}),
            (
                "build type",
                ("predicate", "buildDefinition", "buildType"),
                "not a URI",
            ),
            (
                "external parameters",
                ("predicate", "buildDefinition", "externalParameters"),
                [],
            ),
            (
                "internal parameters",
                ("predicate", "buildDefinition", "internalParameters"),
                [],
            ),
            (
                "resolved dependencies",
                ("predicate", "buildDefinition", "resolvedDependencies"),
                [{"name": "missing-resource-location"}],
            ),
            ("builder id missing", ("predicate", "runDetails", "builder", "id"), ""),
            (
                "builder id is not a URI",
                ("predicate", "runDetails", "builder", "id"),
                "fixture",
            ),
            ("metadata", ("predicate", "runDetails", "metadata"), []),
        )
        for label, path, value in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as name:
                directory = Path(name)

                def mutate(statement, path=path, value=value):
                    replace_nested_value(statement, path, value)

                candidate, layout, receipt, _ = self.fixture(
                    directory, statement_mutation=mutate
                )
                with self.assertRaisesRegex(
                    ValueError, "subject|SLSA provenance|resolved dependencies"
                ):
                    inspect_candidate(
                        candidate=candidate,
                        receipt_path=receipt,
                        layout=layout,
                        scan_layout=directory / "scan-layout",
                    )

    def test_inspection_accepts_valid_optional_slsa_v1_fields(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, _ = self.fixture(
                directory, statement_mutation=add_valid_optional_slsa_fields
            )
            graph = inspect_candidate(
                candidate=candidate,
                receipt_path=receipt,
                layout=layout,
                scan_layout=directory / "scan-layout",
            )
            self.assertEqual(graph["provenance"]["status"], "VALIDATED")

    def test_renderer_produces_contract_compatible_zero_finding_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, config = self.fixture(directory)
            graph_path = directory / "graph.json"
            write_json(
                graph_path,
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout",
                ),
            )
            report = directory / "report.json"
            write_json(
                report,
                {
                    "Metadata": {"ImageID": config["digest"]},
                    "Results": [{"Target": "fixture", "Vulnerabilities": []}],
                },
            )
            version = directory / "version.json"
            write_json(
                version,
                {
                    "Version": "0.74.0",
                    "VulnerabilityDB": {"UpdatedAt": "2026-11-17T18:00:00Z"},
                },
            )
            database = directory / "trivy.db"
            database.write_bytes(b"synthetic trivy database")
            evidence = render_evidence(
                candidate=candidate,
                graph_path=graph_path,
                trivy_report_path=report,
                trivy_version_path=version,
                trivy_database_path=database,
                trivy_binary_path=self.trivy_binary_path(directory),
                scanned_at=dt.datetime(2026, 11, 17, 18, 5, tzinfo=dt.timezone.utc),
            )
            self.assertEqual(evidence["kind"], "room-audio-scan-provenance-v1")
            self.assertEqual(evidence["configDigest"], config["digest"])
            self.assertEqual(
                evidence["scan"]["findings"],
                {"critical": 0, "high": 0, "fixableHighCritical": 0},
            )
            self.assertFalse(evidence["publicationPerformed"])
            self.assertFalse(evidence["deploymentApplied"])
            self.assertEqual(
                evidence["scan"]["scanner"],
                {"name": "Trivy", "version": "0.74.0"},
            )
            self.assertEqual(
                evidence["provenance"],
                {
                    "status": "PASS",
                    "sourceCommit": candidate.source_commit,
                    "sourceTree": candidate.source_tree,
                    "sourceArchiveSha256": candidate.source_archive_sha256,
                },
            )
            self.assertEqual(
                evidence["scannerBinaryDigest"], digest(self.trivy_binary)
            )
            graph_provenance = json.loads(graph_path.read_text())["provenance"]
            self.assertEqual(
                evidence["slsaProvenance"],
                {
                    field: graph_provenance[field]
                    for field in (
                        "attestationManifestDigest",
                        "statementDigest",
                        "predicateType",
                        "subjectDigest",
                    )
                },
            )

            altered_binary = directory / "altered-trivy"
            altered_binary.write_bytes(b"altered")
            with self.assertRaisesRegex(ValueError, "binary digest"):
                render_evidence(
                    candidate=candidate,
                    graph_path=graph_path,
                    trivy_report_path=report,
                    trivy_version_path=version,
                    trivy_database_path=database,
                    trivy_binary_path=altered_binary,
                    scanned_at=dt.datetime(
                        2026, 11, 17, 18, 5, tzinfo=dt.timezone.utc
                    ),
                )

    def test_renderer_rejects_findings_stale_database_or_wrong_config(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, config = self.fixture(directory)
            graph_path = directory / "graph.json"
            write_json(
                graph_path,
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout",
                ),
            )
            database = directory / "trivy.db"
            database.write_bytes(b"db")
            version = directory / "version.json"
            report = directory / "report.json"
            clock = dt.datetime(2026, 11, 17, 18, 5, tzinfo=dt.timezone.utc)
            cases = (
                (
                    {
                        "Metadata": {"ImageID": "sha256:" + "f" * 64},
                        "Results": [{"Target": "fixture", "Vulnerabilities": []}],
                    },
                    {"Version": "0.74.0", "VulnerabilityDB": {"UpdatedAt": "2026-11-17T18:00:00Z"}},
                    "config digest",
                ),
                (
                    {
                        "Metadata": {"ImageID": config["digest"]},
                        "Results": [
                            {
                                "Target": "fixture",
                                "Vulnerabilities": [
                                    {"Severity": "HIGH", "FixedVersion": "2"}
                                ],
                            }
                        ],
                    },
                    {"Version": "0.74.0", "VulnerabilityDB": {"UpdatedAt": "2026-11-17T18:00:00Z"}},
                    "findings",
                ),
                (
                    {
                        "Metadata": {"ImageID": config["digest"]},
                        "Results": [{"Target": "fixture", "Vulnerabilities": []}],
                    },
                    {"Version": "0.74.0", "VulnerabilityDB": {"UpdatedAt": "2026-11-17T11:00:00Z"}},
                    "stale",
                ),
            )
            for scan, scanner, message in cases:
                with self.subTest(message=message):
                    write_json(report, scan)
                    write_json(version, scanner)
                    with self.assertRaisesRegex(ValueError, message):
                        render_evidence(
                            candidate=candidate,
                            graph_path=graph_path,
                            trivy_report_path=report,
                            trivy_version_path=version,
                            trivy_database_path=database,
                            trivy_binary_path=self.trivy_binary_path(directory),
                            scanned_at=clock,
                        )

    def test_renderer_rejects_malformed_vulnerability_collections_and_severities(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            candidate, layout, receipt, config = self.fixture(directory)
            graph_path = directory / "graph.json"
            write_json(
                graph_path,
                inspect_candidate(
                    candidate=candidate,
                    receipt_path=receipt,
                    layout=layout,
                    scan_layout=directory / "scan-layout",
                ),
            )
            version = directory / "version.json"
            write_json(
                version,
                {
                    "Version": "0.74.0",
                    "VulnerabilityDB": {"UpdatedAt": "2026-11-17T18:00:00Z"},
                },
            )
            database = directory / "trivy.db"
            database.write_bytes(b"db")
            report = directory / "report.json"
            cases = (
                ({"Target": "fixture"}, "vulnerabilities"),
                ({"Target": "fixture", "Vulnerabilities": None}, "vulnerabilities"),
                (
                    {
                        "Target": "fixture",
                        "Vulnerabilities": [{"VulnerabilityID": "CVE-1"}],
                    },
                    "severity",
                ),
                (
                    {
                        "Target": "fixture",
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-1", "Severity": "URGENT"}
                        ],
                    },
                    "severity",
                ),
                (
                    {
                        "Target": "fixture",
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-1", "Severity": ["HIGH"]}
                        ],
                    },
                    "severity",
                ),
            )
            for result, message in cases:
                with self.subTest(result=result):
                    write_json(
                        report,
                        {
                            "Metadata": {"ImageID": config["digest"]},
                            "Results": [result],
                        },
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        render_evidence(
                            candidate=candidate,
                            graph_path=graph_path,
                            trivy_report_path=report,
                            trivy_version_path=version,
                            trivy_database_path=database,
                            trivy_binary_path=self.trivy_binary_path(directory),
                            scanned_at=dt.datetime(
                                2026, 11, 17, 18, 5, tzinfo=dt.timezone.utc
                            ),
                        )


class DeploymentsEvidenceContractIntegrationTests(unittest.TestCase):
    def test_renderer_output_is_accepted_by_deployments_check_evidence(self):
        deployments_root_value = os.environ.get("MASTRAO_DEPLOYMENTS_ROOT")
        if deployments_root_value is None:
            self.skipTest(
                "MASTRAO_DEPLOYMENTS_ROOT is required for the cross-repository test"
            )

        deployments_root = Path(deployments_root_value).resolve()
        publisher_path = (
            deployments_root
            / "tools"
            / "production-image-publication"
            / "room-audio-publication.py"
        )
        if not publisher_path.is_file():
            self.fail(f"Deployments publisher not found: {publisher_path}")

        consumer = run_path(str(publisher_path))
        packet, _ = consumer["read_json"](consumer["PACKET"])
        clock = dt.datetime(2026, 11, 17, 18, 5, tzinfo=dt.timezone.utc)
        expires = clock + dt.timedelta(minutes=20)

        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            accepted_images = []
            for index, image in enumerate(packet["images"], start=1):
                candidate = CANDIDATES[image["role"]]
                config_digest = f"sha256:{index:064x}"
                attestation_digest = f"sha256:{index + 10:064x}"
                graph_path = directory / f"{candidate.role}-graph.json"
                write_json(
                    graph_path,
                    {
                        "kind": "mastrao.meet.room-audio-oci-graph.v1",
                        "role": candidate.role,
                        "rootDigest": candidate.root_digest,
                        "configDigest": config_digest,
                        "ociGraphManifestDigests": [
                            candidate.amd64_manifest_digest,
                            attestation_digest,
                        ],
                        "provenance": {
                            "status": "VALIDATED",
                            "attestationManifestDigest": attestation_digest,
                            "statementDigest": f"sha256:{index + 20:064x}",
                            "predicateType": SLSA_PROVENANCE_TYPE,
                            "subjectDigest": candidate.amd64_manifest_digest,
                        },
                    },
                )
                report_path = directory / f"{candidate.role}-report.json"
                write_json(
                    report_path,
                    {
                        "Metadata": {"ImageID": config_digest},
                        "Results": [{"Target": candidate.role, "Vulnerabilities": []}],
                    },
                )
                version_path = directory / f"{candidate.role}-version.json"
                write_json(
                    version_path,
                    {
                        "Version": "0.74.0",
                        "VulnerabilityDB": {"UpdatedAt": "2026-11-17T18:00:00Z"},
                    },
                )
                database_path = directory / f"{candidate.role}-trivy.db"
                database_path.write_bytes(b"synthetic trivy database")
                binary_path = directory / f"{candidate.role}-trivy"
                binary_path.write_bytes(b"synthetic trivy binary")

                with mock.patch.object(
                    evidence_module,
                    "TRIVY_BINARY_DIGEST",
                    digest(binary_path.read_bytes()),
                ):
                    evidence = render_evidence(
                        candidate=candidate,
                        graph_path=graph_path,
                        trivy_report_path=report_path,
                        trivy_version_path=version_path,
                        trivy_database_path=database_path,
                        trivy_binary_path=binary_path,
                        scanned_at=clock,
                    )

                receipt_path = directory / f"{candidate.role}-receipt.json"
                evidence_path = directory / f"{candidate.role}-evidence.json"
                receipt_path.write_text(
                    json.dumps(expected_receipt(candidate), sort_keys=True) + "\n"
                )
                evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
                accepted_images.append(
                    {
                        "role": candidate.role,
                        "configDigest": config_digest,
                        "originalCandidateReceiptFile": receipt_path.name,
                        "originalCandidateReceiptSha256": hashlib.sha256(
                            receipt_path.read_bytes()
                        ).hexdigest(),
                        "scanProvenanceFile": evidence_path.name,
                        "scanProvenanceSha256": hashlib.sha256(
                            evidence_path.read_bytes()
                        ).hexdigest(),
                    }
                )

            consumer["check_evidence"](
                packet,
                {"images": accepted_images},
                directory,
                expires,
                clock,
            )


class RoomAudioProductionEvidenceWorkflowTests(unittest.TestCase):
    def test_workflow_is_read_only_and_fail_closed(self):
        workflow = Path(".github/workflows/room-audio-production-evidence.yml").read_text()
        self.assertIn("role: [meet-agents, meet-backend]", workflow)
        self.assertIn("MEET_STAGING_REGISTRY_READ_PASSWORD", workflow)
        self.assertIn("version: v0.74.0", workflow)
        self.assertIn("--to-oci-layout", workflow)
        self.assertIn("docker logout rg.fr-par.scw.cloud/mastrao-staging", workflow)
        self.assertIn("--trivy-binary", workflow)
        self.assertIn(evidence_module.TRIVY_BINARY_DIGEST, workflow)
        self.assertIn("Upload evidence only", workflow)
        install_position = workflow.index("Install the approved Trivy scanner")
        login_position = workflow.index("Authenticate to the staging registry")
        copy_position = workflow.index("Copy the exact candidate")
        logout_position = workflow.index("Remove registry credentials")
        scan_position = workflow.index("Scan the exact runtime image")
        self.assertLess(install_position, login_position)
        self.assertLess(copy_position, logout_position)
        self.assertLess(logout_position, scan_position)
        for reference in re.findall(r"uses:\s*(\S+)", workflow):
            with self.subTest(reference=reference):
                self.assertRegex(reference, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
        lowered = workflow.lower()
        for forbidden in (
            "docker push",
            "oras push",
            "--from-oci-layout",
            "kubectl",
            "helm ",
            "skopeo copy",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
