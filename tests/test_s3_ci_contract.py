import importlib.util
import unittest
from pathlib import Path

MOTO_IMAGE = (
    "ghcr.io/getmoto/motoserver@"
    "sha256:d8ae5edc2bf080e7e4c13f9bd4b29b53ac3b4427e92956318db3dbe23ec43eb7"
)
WORKFLOW = Path(".github/workflows/meet.yml")
SMOKE = Path("scripts/ci/s3_smoke.py")


class _Body:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class _S3Client:
    def __init__(self, readback=b"mastrao-s3-smoke", remaining=0):
        self.readback = readback
        self.remaining = remaining
        self.calls = []

    def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": _Body(self.readback)}

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list_objects_v2", kwargs))
        return {"KeyCount": self.remaining}


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("s3_smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class S3CIContractTests(unittest.TestCase):
    def test_ci_uses_only_the_immutable_official_moto_image(self):
        workflow = WORKFLOW.read_text()
        self.assertIn(f"S3_EMULATOR_IMAGE: {MOTO_IMAGE}", workflow)
        self.assertNotIn("docker.io/minio/", workflow)
        self.assertNotIn("quay.io/minio/", workflow)
        self.assertNotIn("MINIO_CLIENT_IMAGE", workflow)
        self.assertNotIn("MINIO_SERVER_IMAGE", workflow)

    def test_ci_forces_fresh_pulls_for_both_index_architectures(self):
        workflow = WORKFLOW.read_text()
        remove = 'docker image rm --force "$S3_EMULATOR_IMAGE"'
        self.assertEqual(workflow.count(remove), 2)
        for platform in ("linux/amd64", "linux/arm64"):
            with self.subTest(platform=platform):
                self.assertIn(
                    f'docker pull --platform {platform} "$S3_EMULATOR_IMAGE"',
                    workflow,
                )
        arm64_pull = workflow.index("docker pull --platform linux/arm64")
        intermediate_remove = workflow.index(remove, arm64_pull)
        amd64_pull = workflow.index("docker pull --platform linux/amd64")
        self.assertLess(arm64_pull, intermediate_remove)
        self.assertLess(intermediate_remove, amd64_pull)

    def test_ci_uses_boto3_smoke_instead_of_mc(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("uv run python ../../scripts/ci/s3_smoke.py", workflow)
        self.assertNotIn("mc alias", workflow)
        self.assertNotIn("mc mb", workflow)
        self.assertNotIn("mc cp", workflow)
        self.assertNotIn("mc cat", workflow)
        self.assertNotIn("mc rm", workflow)

        smoke = SMOKE.read_text()
        for operation in (
            "create_bucket",
            "put_object",
            "get_object",
            "delete_object",
            "list_objects_v2",
        ):
            with self.subTest(operation=operation):
                self.assertIn(f"client.{operation}", smoke)

    def test_local_minio_webhook_surfaces_are_unchanged(self):
        compose = Path("compose.yml").read_text()
        helm = Path("src/helm/extra/templates/minio.yaml").read_text()
        for surface in (compose, helm):
            with self.subTest():
                self.assertIn("quay.io/minio/minio@sha256:", surface)
                self.assertIn("quay.io/minio/mc@sha256:", surface)
                self.assertIn("notify_webhook:meet-webhook", surface)

    def test_smoke_executes_the_full_round_trip_in_order(self):
        client = _S3Client()
        _load_smoke_module().run_smoke(client)
        self.assertEqual(
            [name for name, _ in client.calls],
            [
                "create_bucket",
                "put_object",
                "get_object",
                "delete_object",
                "list_objects_v2",
            ],
        )

    def test_smoke_rejects_mismatched_readback_and_still_cleans_up(self):
        client = _S3Client(readback=b"wrong")
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            _load_smoke_module().run_smoke(client)
        self.assertEqual(client.calls[-1][0], "delete_object")

    def test_smoke_rejects_an_object_left_after_delete(self):
        client = _S3Client(remaining=1)
        with self.assertRaisesRegex(RuntimeError, "still exists"):
            _load_smoke_module().run_smoke(client)


if __name__ == "__main__":
    unittest.main()
