import json
import tempfile
import unittest
from pathlib import Path

from scripts.ci.room_audio_production_evidence import expected_receipt
from scripts.ci.worker_general_production_evidence import (
    ARTIFACT_ID,
    ARTIFACT_NAME,
    CANDIDATE,
    ROOT_DIGEST,
    RUN_ID,
    SOURCE_COMMIT,
    TARGET_REPOSITORY,
    github_outputs,
    render_selection_receipt,
    verify_artifact_metadata,
    verify_candidate_receipt,
)


def write_json(path, value):
    path.write_text(json.dumps(value, separators=(",", ":")))


def graph():
    return {
        "schemaVersion": 1,
        "kind": "mastrao.meet.room-audio-oci-graph.v1",
        "role": "meet-backend",
        "rootDigest": ROOT_DIGEST,
        "ociGraphManifestDigests": [CANDIDATE.amd64_manifest_digest],
    }


def scan():
    return {
        "schemaVersion": 1,
        "kind": "room-audio-scan-provenance-v1",
        "role": "meet-backend",
        "rootDigest": ROOT_DIGEST,
        "scan": {
            "status": "PASS",
            "findings": {"critical": 0, "high": 0, "fixableHighCritical": 0},
        },
        "evidenceValidUntil": "2026-11-18T23:00:00Z",
        "provenance": {
            "status": "PASS",
            "sourceCommit": CANDIDATE.source_commit,
            "sourceTree": CANDIDATE.source_tree,
            "sourceArchiveSha256": CANDIDATE.source_archive_sha256,
        },
        "publicationPerformed": False,
        "deploymentApplied": False,
    }


class WorkerGeneralProductionEvidenceTests(unittest.TestCase):
    def fixture(self, directory):
        receipt_path = directory / "candidate-receipt.json"
        graph_path = directory / "oci-graph.json"
        scan_path = directory / "scan-provenance.json"
        write_json(receipt_path, expected_receipt(CANDIDATE))
        write_json(graph_path, graph())
        write_json(scan_path, scan())
        return receipt_path, graph_path, scan_path

    def test_selection_is_closed_to_one_candidate_and_workload(self):
        outputs = github_outputs()
        self.assertIn(f"source_sha={SOURCE_COMMIT}\n", outputs)
        self.assertIn(f"artifact_id={ARTIFACT_ID}\n", outputs)
        self.assertIn(f"root_digest={ROOT_DIGEST}\n", outputs)
        self.assertIn(f"target_reference={TARGET_REPOSITORY}@{ROOT_DIGEST}\n", outputs)
        self.assertNotIn("password", outputs.lower())

    def test_artifact_metadata_must_match_exact_run_artifact_and_name(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "artifact.json"
            metadata = {
                "id": ARTIFACT_ID,
                "name": ARTIFACT_NAME,
                "expired": False,
                "workflow_run": {"id": RUN_ID},
            }
            write_json(path, metadata)
            verify_artifact_metadata(path)
            for field, changed in (
                ("id", ARTIFACT_ID + 1),
                ("name", "other"),
                ("expired", True),
            ):
                with self.subTest(field=field):
                    write_json(path, {**metadata, field: changed})
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        verify_artifact_metadata(path)

    def test_candidate_receipt_refuses_a_different_digest(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "receipt.json"
            value = expected_receipt(CANDIDATE)
            write_json(path, value)
            verify_candidate_receipt(path)
            value["image"]["digest"] = "sha256:" + "0" * 64
            write_json(path, value)
            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_candidate_receipt(path)

    def test_receipt_binds_only_worker_general_at_zero_replicas(self):
        with tempfile.TemporaryDirectory() as name:
            paths = self.fixture(Path(name))
            receipt = render_selection_receipt(
                candidate_receipt_path=paths[0],
                graph_path=paths[1],
                scan_provenance_path=paths[2],
            )
            self.assertEqual(
                receipt["status"], "SELECTED_SCANNED_NOT_PUBLISHED_NOT_DEPLOYED"
            )
            self.assertEqual(
                receipt["target"],
                {
                    "namespace": "mastrao-production",
                    "kind": "Deployment",
                    "name": "worker-general",
                    "container": "worker",
                    "replicas": 0,
                    "repository": TARGET_REPOSITORY,
                    "reference": f"{TARGET_REPOSITORY}@{ROOT_DIGEST}",
                },
            )
            for fact in (
                "rebuildPerformed",
                "publicationPerformed",
                "deploymentApplied",
                "activationPerformed",
                "dnsChanged",
                "tlsChanged",
            ):
                self.assertFalse(receipt[fact])
            serialized = json.dumps(receipt)
            self.assertNotIn("meet-api", serialized)
            self.assertNotIn("worker-room-audio-processing", serialized)

    def test_receipt_refuses_published_scan(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            receipt_path, graph_path, scan_path = self.fixture(directory)
            value = scan()
            value["publicationPerformed"] = True
            write_json(scan_path, value)
            with self.assertRaisesRegex(ValueError, "does not match"):
                render_selection_receipt(
                    candidate_receipt_path=receipt_path,
                    graph_path=graph_path,
                    scan_provenance_path=scan_path,
                )


class WorkerGeneralProductionWorkflowTests(unittest.TestCase):
    def test_workflow_is_read_only_and_never_builds_publishes_or_deploys(self):
        workflow = Path(
            ".github/workflows/worker-general-production-evidence.yml"
        ).read_text()
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("github.ref == 'refs/heads/develop'", workflow)
        self.assertIn("github.ref_protected == true", workflow)
        self.assertIn("MEET_STAGING_REGISTRY_READ_PASSWORD", workflow)
        self.assertIn("oras cp --to-oci-layout", workflow)
        self.assertIn("--ignore-unfixed", workflow)
        self.assertIn("scripts.ci.worker_general_production_evidence inspect", workflow)
        self.assertIn(
            "scripts.ci.worker_general_production_evidence render-scan", workflow
        )
        self.assertNotIn("scripts.ci.room_audio_production_evidence", workflow)
        self.assertNotIn("build-push-action", workflow)
        self.assertNotIn("docker build", workflow)
        self.assertNotIn("oras push", workflow)
        self.assertNotIn("mastrao-production/meet-backend:", workflow)
        self.assertNotIn("kubectl", workflow)


if __name__ == "__main__":
    unittest.main()
