import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_receipt import build_receipt

SHA = "a" * 40
TREE = "c" * 40
ARCHIVE = "d" * 64
DIGEST = "sha256:" + "b" * 64


class StagingCandidateReceiptTests(unittest.TestCase):
    def test_receipt_is_digest_pinned_and_never_claims_a_deployment(self):
        for target in ("meet-frontend", "meet-backend"):
            with self.subTest(target=target):
                receipt = build_receipt(
                    target=target,
                    source_sha=SHA,
                    source_tree=TREE,
                    source_archive_sha256=ARCHIVE,
                    digest=DIGEST,
                    readback={
                        "schema": "mastrao.meet.staging-candidate-readback.v1",
                        "status": "VERIFIED",
                        "digest": DIGEST,
                        "platform": "linux/amd64",
                    },
                    run_id="123",
                    run_attempt="2",
                )
                self.assertEqual(receipt["target"], target)
                self.assertTrue(receipt["image"]["reference"].endswith("@" + DIGEST))
                self.assertEqual(receipt["status"], "PUBLISHED_NOT_DEPLOYED")
                self.assertEqual(receipt["source"]["tree"], TREE)
                self.assertEqual(receipt["registryReadback"]["status"], "VERIFIED")
                self.assertIs(receipt["deploymentApplied"], False)
                self.assertNotIn("secret", json.dumps(receipt).lower())

    def test_receipt_rejects_open_or_unpinned_values(self):
        for field, value in (
            ("target", "all"),
            ("source_sha", "abc"),
            ("digest", "latest"),
            (
                "readback",
                {
                    "schema": "mastrao.meet.staging-candidate-readback.v1",
                    "status": "VERIFIED",
                    "digest": "sha256:" + "e" * 64,
                    "platform": "linux/amd64",
                },
            ),
            ("run_id", "private"),
        ):
            with self.subTest(field=field):
                values = {
                    "target": "meet-frontend",
                    "source_sha": SHA,
                    "source_tree": TREE,
                    "source_archive_sha256": ARCHIVE,
                    "digest": DIGEST,
                    "readback": {
                        "schema": "mastrao.meet.staging-candidate-readback.v1",
                        "status": "VERIFIED",
                        "digest": DIGEST,
                        "platform": "linux/amd64",
                    },
                    "run_id": "123",
                    "run_attempt": "1",
                }
                values[field] = value
                with self.assertRaises(ValueError):
                    build_receipt(**values)

    def test_cli_writes_only_the_closed_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            readback = Path(directory) / "readback.json"
            readback.write_text(
                json.dumps(
                    {
                        "schema": "mastrao.meet.staging-candidate-readback.v1",
                        "status": "VERIFIED",
                        "digest": DIGEST,
                        "platform": "linux/amd64",
                    }
                )
            )
            subprocess.run(
                [
                    sys.executable,
                    "scripts/ci/staging_candidate_receipt.py",
                    "--target",
                    "meet-backend",
                    "--source-sha",
                    SHA,
                    "--source-tree",
                    TREE,
                    "--source-archive-sha256",
                    ARCHIVE,
                    "--digest",
                    DIGEST,
                    "--readback",
                    str(readback),
                    "--run-id",
                    "123",
                    "--run-attempt",
                    "1",
                    "--output",
                    str(output),
                ],
                check=True,
            )
            self.assertIs(json.loads(output.read_text())["deploymentApplied"], False)


if __name__ == "__main__":
    unittest.main()
