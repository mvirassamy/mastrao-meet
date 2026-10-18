"""Exercise the workflow's archive-to-scanner boundary without a registry."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from tests.test_staging_candidate_workflow import WORKFLOW, run_scripts, steps


def candidate_archive(path):
    files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}

    def blob(value, media_type):
        raw = json.dumps(value, separators=(",", ":")).encode()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        files["blobs/sha256/" + digest.removeprefix("sha256:")] = raw
        return {"mediaType": media_type, "digest": digest, "size": len(raw)}

    manifest_type = "application/vnd.oci.image.manifest.v1+json"
    index_type = "application/vnd.oci.image.index.v1+json"
    config_type = "application/vnd.oci.image.config.v1+json"
    image = blob(
        {
            "schemaVersion": 2,
            "mediaType": manifest_type,
            "config": blob({"os": "linux", "architecture": "amd64"}, config_type),
            "layers": [],
        },
        manifest_type,
    )
    image["platform"] = {"os": "linux", "architecture": "amd64"}
    attestation = blob(
        {
            "schemaVersion": 2,
            "mediaType": manifest_type,
            "config": blob({"os": "unknown"}, config_type),
            "layers": [],
        },
        manifest_type,
    )
    attestation["annotations"] = {
        "vnd.docker.reference.type": "attestation-manifest",
        "vnd.docker.reference.digest": image["digest"],
    }
    # Trivy defaults to the first descriptor. Put the attestation first to
    # prove that the scan projection selects the runtime image explicitly.
    index = blob(
        {
            "schemaVersion": 2,
            "mediaType": index_type,
            "manifests": [attestation, image],
        },
        index_type,
    )
    files["index.json"] = json.dumps(
        {"schemaVersion": 2, "mediaType": index_type, "manifests": [index]}
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return index["digest"], image


class StagingCandidateArchiveTests(unittest.TestCase):
    def run_preparation(self, directory, digest):
        script = run_scripts(
            steps(WORKFLOW.read_text())["Verify and bind the local candidate archive"]
        )[0].split("\n", 1)[1]
        binary = directory / "bin"
        binary.mkdir()
        (binary / "python").symlink_to(sys.executable)
        return subprocess.run(
            ["bash", "-eo", "pipefail", "-c", script],
            env={
                **os.environ,
                "PATH": f"{binary}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(directory),
                "DIGEST": digest,
                "REPOSITORY": "fixture.invalid/meet-backend",
            },
            capture_output=True,
            check=False,
        )

    def test_scan_layout_selects_runtime_manifest_and_preserves_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "meet-candidate.tar"
            digest, image = candidate_archive(archive)
            original = archive.read_bytes()
            result = self.run_preparation(directory, digest)
            self.assertEqual(result.returncode, 0, result.stderr)
            layout = directory / "candidate-layout"
            index = json.loads((layout / "index.json").read_text())
            self.assertEqual(index["manifests"], [image])
            self.assertTrue((layout / "oci-layout").is_file())
            for blob in (layout / "blobs/sha256").iterdir():
                self.assertEqual(
                    hashlib.sha256(blob.read_bytes()).hexdigest(), blob.name
                )
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(
                json.loads((directory / "candidate-local-readback.json").read_text())[
                    "digest"
                ],
                digest,
            )

    def test_wrong_build_digest_cannot_create_a_scan_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate_archive(directory / "meet-candidate.tar")
            result = self.run_preparation(directory, "sha256:" + "f" * 64)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "candidate-layout").exists())


if __name__ == "__main__":
    unittest.main()
