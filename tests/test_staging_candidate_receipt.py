import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.ci.staging_candidate_readback import readback_proof
from scripts.ci.staging_candidate_receipt import (
    Publication,
    SourceIdentity,
    WorkflowRun,
    build_receipt,
)
from scripts.ci.staging_candidate_recipe import TARGETS

SOURCE = SourceIdentity(sha="a" * 40, tree="c" * 40, archive_sha256="d" * 64)
DIGEST = "sha256:" + "b" * 64
IMAGE = "sha256:" + "f" * 64
RUN = WorkflowRun(run_id="123", run_attempt="1")


def readback_for(target, digest=DIGEST):
    return readback_proof(
        repository=TARGETS[target].repository,
        index_digest=digest,
        image_digest=IMAGE,
    )


def valid_values(target="meet-frontend"):
    return {
        "target": target,
        "source": SOURCE,
        "built": TARGETS[target],
        "publication": Publication(digest=DIGEST, readback=readback_for(target)),
        "run": RUN,
    }


def publication(readback):
    return Publication(digest=DIGEST, readback=readback)


class StagingCandidateReceiptTests(unittest.TestCase):
    def test_receipt_is_digest_pinned_and_never_claims_a_deployment(self):
        for target, recipe in TARGETS.items():
            with self.subTest(target=target):
                receipt = build_receipt(**valid_values(target))
                self.assertEqual(receipt["target"], target)
                self.assertEqual(
                    receipt["image"]["reference"], f"{recipe.repository}@{DIGEST}"
                )
                self.assertEqual(receipt["status"], "PUBLISHED_NOT_DEPLOYED")
                self.assertEqual(receipt["source"]["tree"], SOURCE.tree)
                self.assertEqual(
                    receipt["recipe"]["buildArgs"], list(recipe.build_args)
                )
                self.assertEqual(
                    receipt["registryReadback"]["repository"], recipe.repository
                )
                self.assertIs(receipt["deploymentApplied"], False)
                self.assertNotIn("secret", json.dumps(receipt).lower())

    def test_receipt_rejects_open_or_unpinned_values(self):
        frontend = TARGETS["meet-frontend"]
        readback = readback_for("meet-frontend")
        cases = (
            ("target", "all", "unsupported candidate target"),
            ("source", replace(SOURCE, sha="abc"), "source SHA"),
            ("source", replace(SOURCE, sha="A" * 40), "source SHA"),
            ("source", replace(SOURCE, tree="z" * 40), "source tree"),
            ("source", replace(SOURCE, archive_sha256="d" * 63), "archive digest"),
            ("built", replace(frontend, repository="docker.io/x"), "built recipe"),
            ("built", replace(frontend, dockerfile="Dockerfile"), "built recipe"),
            (
                "built",
                replace(frontend, build_args=("DOCKER_USER=0:0",)),
                "built recipe",
            ),
            (
                "built",
                replace(frontend, build_args=frontend.build_args[:-1]),
                "built recipe",
            ),
            ("built", TARGETS["meet-backend"], "built recipe"),
            (
                "publication",
                Publication(digest="latest", readback=readback),
                "sha256 OCI digest",
            ),
            (
                "publication",
                publication(readback_for("meet-frontend", "sha256:" + "e" * 64)),
                "registry readback",
            ),
            (
                "publication",
                publication({**readback, "repository": "docker.io/x"}),
                "registry readback",
            ),
            (
                "publication",
                publication({**readback, "status": "FAILED"}),
                "registry readback",
            ),
            (
                "publication",
                publication({**readback, "imageDigest": "latest"}),
                "registry readback",
            ),
            (
                "publication",
                publication({k: v for k, v in readback.items() if k != "imageDigest"}),
                "registry readback",
            ),
            ("publication", publication(None), "registry readback"),
            ("run", replace(RUN, run_id="private"), "numeric"),
            ("run", replace(RUN, run_attempt="1.5"), "numeric"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                values = {**valid_values(), field: value}
                with self.assertRaisesRegex(ValueError, message):
                    build_receipt(**values)

    def run_cli(self, directory, readback_text, build_args):
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
                recipe.dockerfile,
                "--build-target",
                recipe.build_target,
                "--repository",
                recipe.repository,
                "--build-args",
                build_args,
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
        build_args = "\n".join(TARGETS["meet-backend"].build_args) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.run_cli(
                directory, json.dumps(readback_for("meet-backend")), build_args
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(output.read_text())
            self.assertIs(receipt["deploymentApplied"], False)
            self.assertEqual(
                receipt["recipe"]["buildArgs"],
                list(TARGETS["meet-backend"].build_args),
            )

    def test_cli_fails_without_output_on_bad_inputs(self):
        valid_readback = json.dumps(readback_for("meet-backend"))
        build_args = "\n".join(TARGETS["meet-backend"].build_args)
        cases = {
            "unreadable readback": ("{not json", build_args),
            "drifted build args": (valid_readback, "DOCKER_USER=0:0"),
        }
        for name, (readback_text, args) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                result, output = self.run_cli(directory, readback_text, args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
