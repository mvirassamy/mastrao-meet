from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LiveKitRendererContractTests(unittest.TestCase):
    def test_dockerfile_exposes_one_dedicated_unprivileged_target(self):
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn("FROM core AS livekit-render-production", dockerfile)
        self.assertIn(
            'ENTRYPOINT ["/app/.venv/bin/python", "-m", "livekit_config_renderer"]',
            dockerfile,
        )
        target = dockerfile.split("FROM core AS livekit-render-production", 1)[1]
        self.assertIn("USER ${DOCKER_USER}", target)
        self.assertNotIn("CMD ", target)

    def test_renderer_source_has_no_environment_specific_values(self):
        source = (ROOT / "src/backend/livekit_config_renderer.py").read_text()

        self.assertNotIn("mastrao-staging", source)
        self.assertNotIn("mastrao-production", source)
        self.assertNotIn("svc.cluster.local", source)
        self.assertNotIn("LIVEKIT_API_KEY", source)
        self.assertNotIn("LIVEKIT_API_SECRET", source)


if __name__ == "__main__":
    unittest.main()
