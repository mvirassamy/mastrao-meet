import json
import unittest

from scripts.ci.staging_candidate_readback import verify_readback


class StagingCandidateReadbackTests(unittest.TestCase):
    def test_verifies_the_remote_digest_and_linux_amd64_manifest(self):
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "digest": "sha256:" + "a" * 64,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": "sha256:" + "b" * 64,
                    "platform": {"os": "unknown", "architecture": "unknown"},
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest"
                    },
                },
            ],
        }
        raw = json.dumps(manifest, separators=(",", ":")).encode()
        digest = "sha256:" + "c" * 64
        self.assertEqual(
            verify_readback(raw=raw, expected_digest=digest),
            {
                "schema": "mastrao.meet.staging-candidate-readback.v1",
                "status": "VERIFIED",
                "digest": digest,
                "platform": "linux/amd64",
            },
        )

    def test_rejects_digest_platform_or_attestation_drift(self):
        media_type = "application/vnd.oci.image.index.v1+json"
        attestation = {
            "digest": "sha256:" + "b" * 64,
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
        }
        cases = [
            (
                {
                    "schemaVersion": 2,
                    "mediaType": media_type,
                    "manifests": [
                        {
                            "digest": "sha256:" + "a" * 64,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        },
                        attestation,
                    ],
                },
                "sha256:invalid",
            ),
            (
                {
                    "schemaVersion": 2,
                    "mediaType": media_type,
                    "manifests": [
                        {
                            "digest": "sha256:" + "a" * 64,
                            "platform": {"os": "linux", "architecture": "arm64"},
                        },
                        attestation,
                    ],
                },
                None,
            ),
            (
                {
                    "schemaVersion": 2,
                    "mediaType": media_type,
                    "manifests": [
                        {
                            "digest": "sha256:" + "a" * 64,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }
                    ],
                },
                None,
            ),
        ]
        for manifest, forced_digest in cases:
            with self.subTest(manifest=manifest):
                raw = json.dumps(manifest).encode()
                digest = forced_digest or "sha256:" + "c" * 64
                with self.assertRaises((TypeError, ValueError)):
                    verify_readback(raw=raw, expected_digest=digest)


if __name__ == "__main__":
    unittest.main()
