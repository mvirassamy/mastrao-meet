import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.ci.staging_candidate_readback import readback_proof
from scripts.ci.staging_candidate_receipt import (
    TARGETS,
    BuiltRecipe,
    SourceIdentity,
    build_receipt,
)

SOURCE = SourceIdentity(sha="a" * 40, tree="c" * 40, archive_sha256="d" * 64)
DIGEST = "sha256:" + "b" * 64
IMAGE = "sha256:" + "f" * 64


def built_for(target):
    return BuiltRecipe(**TARGETS[target])


def readback_for(target, digest=DIGEST):
    return readback_proof(
        repository=TARGETS[target]["repository"],
        index_digest=digest,
        image_digest=IMAGE,
    )


def valid_values(target="meet-frontend"):
    return {
        "target": target,
        "source": SOURCE,
        "built": built_for(target),
        "digest": DIGEST,
        "readback": readback_for(target),
        "run_id": "123",
        "run_attempt": "1",
    }


class StagingCandidateReceiptTests(unittest.TestCase):
    def test_receipt_is_digest_pinned_and_never_claims_a_deployment(self):
        for target in TARGETS:
            with self.subTest(target=target):
                receipt = build_receipt(**valid_values(target))
                repository = TARGETS[target]["repository"]
                self.assertEqual(receipt["target"], target)
                self.assertEqual(
                    receipt["image"]["reference"], f"{repository}@{DIGEST}"
                )
                self.assertEqual(receipt["status"], "PUBLISHED_NOT_DEPLOYED")
                self.assertEqual(receipt["source"]["tree"], SOURCE.tree)
                self.assertEqual(receipt["registryReadback"]["repository"], repository)
                self.assertIs(receipt["deploymentApplied"], False)
                self.assertNotIn("secret", json.dumps(receipt).lower())

    def test_receipt_rejects_open_or_unpinned_values(self):
        frontend = readback_for("meet-frontend")
        cases = (
            ("target", "all", "unsupported candidate target"),
            ("source", replace(SOURCE, sha="abc"), "source SHA"),
            ("source", replace(SOURCE, sha="A" * 40), "source SHA"),
            ("source", replace(SOURCE, tree="z" * 40), "source tree"),
            ("source", replace(SOURCE, archive_sha256="d" * 63), "archive digest"),
            ("digest", "latest", "sha256 OCI digest"),
            (
                "built",
                replace(built_for("meet-frontend"), repository="docker.io/x"),
                "built recipe",
            ),
            ("built", built_for("meet-backend"), "built recipe"),
            (
                "readback",
                readback_for("meet-frontend", "sha256:" + "e" * 64),
                "registry readback",
            ),
            (
                "readback",
                {**frontend, "repository": "docker.io/x"},
                "registry readback",
            ),
            ("readback", {**frontend, "status": "FAILED"}, "registry readback"),
            ("readback", {**frontend, "imageDigest": "latest"}, "registry readback"),
            (
                "readback",
                {k: v for k, v in frontend.items() if k != "imageDigest"},
                "registry readback",
            ),
            ("readback", None, "registry readback"),
            ("run_id", "private", "numeric"),
            ("run_attempt", "1.5", "numeric"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                values = {**valid_values(), field: value}
                with self.assertRaisesRegex(ValueError, message):
                    build_receipt(**values)

    def run_cli(self, directory, readback_text, repository):
        output = Path(directory) / "receipt.json"
        readback = Path(directory) / "readback.json"
        readback.write_text(readback_text)
        recipe = TARGETS["meet-backend"]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ci.staging_candidate_receipt",
                "--target",
                "meet-backend",
                "--source-sha",
                SOURCE.sha,
                "--source-tree",
                SOURCE.tree,
                "--source-archive-sha256",
                SOURCE.archive_sha256,
                "--dockerfile",
                recipe["dockerfile"],
                "--build-target",
                recipe["target"],
                "--repository",
                repository,
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
            capture_output=True,
            check=False,
        )
        return result, output

    def test_cli_writes_only_the_closed_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.run_cli(
                directory,
                json.dumps(readback_for("meet-backend")),
                TARGETS["meet-backend"]["repository"],
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIs(json.loads(output.read_text())["deploymentApplied"], False)

    def test_cli_fails_without_output_on_bad_inputs(self):
        cases = {
            "unreadable readback": ("{not json", TARGETS["meet-backend"]["repository"]),
            "drifted repository": (
                json.dumps(readback_for("meet-backend")),
                "rg.fr-par.scw.cloud/mastrao-staging/other",
            ),
        }
        for name, (readback_text, repository) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                result, output = self.run_cli(directory, readback_text, repository)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
