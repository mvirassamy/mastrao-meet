import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_readback import (
    MAX_MANIFEST_BYTES,
    READBACK_SCHEMA,
    verify_readback,
)

REPOSITORY = "rg.fr-par.scw.cloud/mastrao-staging/meet-backend"
IMAGE = "sha256:" + "a" * 64
OTHER = "sha256:" + "e" * 64
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"


def image(digest=IMAGE, architecture="amd64"):
    return {
        "digest": digest,
        "platform": {"os": "linux", "architecture": architecture},
    }


def attestation(reference=IMAGE, digest="sha256:" + "b" * 64):
    return {
        "digest": digest,
        "platform": {"os": "unknown", "architecture": "unknown"},
        "annotations": {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": reference,
        },
    }


def index(*descriptors, **overrides):
    manifest = {
        "schemaVersion": 2,
        "mediaType": INDEX_TYPE,
        "manifests": list(descriptors),
    }
    manifest.update(overrides)
    return json.dumps(manifest, separators=(",", ":")).encode()


def digest_of(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class StagingCandidateReadbackTests(unittest.TestCase):
    def test_verifies_the_exact_index_bytes_and_its_attested_image(self):
        raw = index(image(), attestation())
        digest = digest_of(raw)
        self.assertEqual(
            verify_readback(raw=raw, repository=REPOSITORY, expected_digest=digest),
            {
                "schema": READBACK_SCHEMA,
                "status": "VERIFIED",
                "repository": REPOSITORY,
                "digest": digest,
                "imageDigest": IMAGE,
                "platform": "linux/amd64",
            },
        )

    def test_rejects_bytes_that_do_not_hash_to_the_expected_digest(self):
        raw = index(image(), attestation())
        with self.assertRaisesRegex(ValueError, "do not match the expected digest"):
            verify_readback(
                raw=raw, repository=REPOSITORY, expected_digest="sha256:" + "c" * 64
            )

    def test_rejects_malformed_expected_digests(self):
        raw = index(image(), attestation())
        for digest in ("sha256:invalid", digest_of(raw).upper(), "latest"):
            with (
                self.subTest(digest=digest),
                self.assertRaisesRegex(ValueError, "sha256 OCI digest"),
            ):
                verify_readback(raw=raw, repository=REPOSITORY, expected_digest=digest)

    def test_rejects_empty_or_oversized_payloads(self):
        for raw in (b"", b" " * (MAX_MANIFEST_BYTES + 1)):
            with (
                self.subTest(size=len(raw)),
                self.assertRaisesRegex(ValueError, "empty or too large"),
            ):
                verify_readback(
                    raw=raw, repository=REPOSITORY, expected_digest=digest_of(raw)
                )

    def test_rejects_payloads_that_are_not_a_supported_index(self):
        cases = {
            "invalid json": (b"{not json", "not valid JSON"),
            "json list": (b"[]", "schema is unsupported"),
            "schema v1": (
                index(image(), attestation(), schemaVersion=1),
                "unsupported",
            ),
            "single manifest": (
                index(
                    image(),
                    attestation(),
                    mediaType="application/vnd.oci.image.manifest.v1+json",
                ),
                "unsupported",
            ),
            "manifests not a list": (
                index(image(), manifests={"digest": IMAGE}),
                "descriptors are malformed",
            ),
            "descriptor not an object": (
                index(image(), attestation(), "sha256:" + "f" * 64),
                "descriptors are malformed",
            ),
            "descriptor digest malformed": (
                index(image(digest="sha256:bad"), attestation()),
                "descriptors are malformed",
            ),
        }
        for name, (raw, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                verify_readback(
                    raw=raw, repository=REPOSITORY, expected_digest=digest_of(raw)
                )

    def test_rejects_platform_or_attestation_drift(self):
        dual_role = image()
        dual_role["annotations"] = attestation()["annotations"]
        cases = {
            "arm64 only": (
                index(image(architecture="arm64"), attestation()),
                "not linux/amd64",
            ),
            "extra platform": (
                index(image(), image(OTHER, "arm64"), attestation()),
                "exactly one image manifest",
            ),
            "no attestation": (index(image()), "no attestation"),
            "attestation for another image": (
                index(image(), attestation(reference=OTHER)),
                "no attestation",
            ),
            "image doubling as attestation": (
                index(dual_role),
                "exactly one image manifest",
            ),
        }
        for name, (raw, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                verify_readback(
                    raw=raw, repository=REPOSITORY, expected_digest=digest_of(raw)
                )


class StagingCandidateReadbackCliTests(unittest.TestCase):
    def run_cli(self, directory, raw, digest):
        manifest = Path(directory) / "index.json"
        output = Path(directory) / "readback.json"
        manifest.write_bytes(raw)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ci.staging_candidate_readback",
                "--manifest",
                str(manifest),
                "--repository",
                REPOSITORY,
                "--expected-digest",
                digest,
                "--output",
                str(output),
            ],
            capture_output=True,
            check=False,
        )
        return result, output

    def test_cli_writes_the_verified_proof(self):
        raw = index(image(), attestation())
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.run_cli(directory, raw, digest_of(raw))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output.read_text()),
                verify_readback(
                    raw=raw, repository=REPOSITORY, expected_digest=digest_of(raw)
                ),
            )

    def test_cli_fails_without_output_on_a_digest_mismatch(self):
        raw = index(image(), attestation())
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.run_cli(directory, raw, "sha256:" + "c" * 64)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
