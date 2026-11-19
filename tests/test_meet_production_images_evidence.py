import datetime as dt
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.ci.meet_production_images_evidence import (
    SELECTIONS,
    inspect_layout,
    render_evidence,
    select,
)


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptor(raw: bytes, media_type: str, **extra: object) -> dict[str, object]:
    return {
        "digest": digest(raw),
        "mediaType": media_type,
        "size": len(raw),
        **extra,
    }


class MeetProductionImagesEvidenceTests(unittest.TestCase):
    def test_closed_selections_use_exact_production_digests(self):
        self.assertEqual(set(SELECTIONS), {"meet-frontend", "meet-api"})
        for role, selection in SELECTIONS.items():
            output = select(role)
            self.assertIn(f"role={role}\n", output)
            self.assertIn(f"reference={selection.reference}\n", output)
            self.assertTrue(
                selection.reference.startswith(
                    "rg.fr-par.scw.cloud/mastrao-production/"
                )
            )

    def test_inspect_binds_complete_runtime_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = root / "layout"
            blobs = layout / "blobs" / "sha256"
            blobs.mkdir(parents=True)
            (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')

            config = b'{"architecture":"amd64","os":"linux"}'
            layer = b"runtime-layer"
            config_descriptor = descriptor(
                config, "application/vnd.oci.image.config.v1+json"
            )
            layer_descriptor = descriptor(
                layer, "application/vnd.oci.image.layer.v1.tar+gzip"
            )
            manifest = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": config_descriptor,
                    "layers": [layer_descriptor],
                },
                separators=(",", ":"),
            ).encode()
            manifest_descriptor = descriptor(
                manifest,
                "application/vnd.oci.image.manifest.v1+json",
                platform={"os": "linux", "architecture": "amd64"},
            )
            index = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": [manifest_descriptor],
                },
                separators=(",", ":"),
            ).encode()
            selection = SELECTIONS["meet-api"]
            selected_digest = digest(index)
            with patch.dict(
                SELECTIONS,
                {"meet-api": replace(selection, digest=selected_digest)},
            ):
                for raw in (config, layer, manifest, index):
                    (blobs / digest(raw).removeprefix("sha256:")).write_bytes(raw)
                (layout / "index.json").write_text(
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "manifests": [
                                descriptor(
                                    index,
                                    "application/vnd.oci.image.index.v1+json",
                                )
                            ],
                        }
                    )
                )
                graph = inspect_layout(
                    role="meet-api",
                    layout=layout,
                    scan_layout=root / "scan",
                )

            self.assertEqual(graph["status"], "EXACT_PRODUCTION_DIGEST_READ_BACK")
            self.assertEqual(graph["runtimeManifestDigest"], digest(manifest))
            self.assertTrue((root / "scan" / "index.json").is_file())

    def test_render_records_scan_sbom_and_signature_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = SELECTIONS["meet-frontend"]
            graph = root / "graph.json"
            report = root / "report.json"
            sbom = root / "sbom.json"
            version = root / "version.json"
            graph_payload = {
                "status": "EXACT_PRODUCTION_DIGEST_READ_BACK",
                "role": selection.role,
                "rootDigest": selection.digest,
                "reference": selection.reference,
                "configDigest": "sha256:" + "a" * 64,
            }
            graph.write_text(json.dumps(graph_payload))
            report.write_text(
                json.dumps(
                    {
                        "Metadata": {"ImageID": graph_payload["configDigest"]},
                        "Results": [],
                    }
                )
            )
            sbom.write_text('{"bomFormat":"CycloneDX","components":[]}')
            now = dt.datetime.now(dt.timezone.utc)
            version.write_text(
                json.dumps(
                    {
                        "Version": "0.74.0",
                        "VulnerabilityDB": {
                            "UpdatedAt": (now - dt.timedelta(hours=1)).isoformat(),
                            "DownloadedAt": now.isoformat(),
                            "NextUpdate": (now + dt.timedelta(hours=5)).isoformat(),
                        },
                    }
                )
            )

            evidence = render_evidence(
                role=selection.role,
                graph_path=graph,
                vulnerability_report_path=report,
                sbom_path=sbom,
                trivy_version_path=version,
                scanned_at=now,
            )

            self.assertEqual(
                evidence["status"],
                "READ_ONLY_IMAGE_EVIDENCE_CAPTURED_SIGNATURE_NOT_PROVEN",
            )
            self.assertEqual(
                evidence["signatureVerification"]["reason"],
                "NO_MEET_IMAGE_SIGNATURE_TRUST_POLICY",
            )
            self.assertFalse(evidence["registryMutationPerformed"])

    def test_workflow_is_read_only_and_has_no_build_or_publish_step(self):
        workflow = Path(
            ".github/workflows/meet-production-images-evidence.yml"
        ).read_text()
        self.assertIn("environment: meet-production-evidence", workflow)
        self.assertIn("--to-oci-layout", workflow)
        self.assertNotIn("docker/build-push-action", workflow)
        self.assertNotIn("oras push", workflow)
        self.assertNotIn("oras copy", workflow)
        self.assertNotIn("permissions:\n  packages: write", workflow)


if __name__ == "__main__":
    unittest.main()
