import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/staging-candidate.yml")


class StagingCandidateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_exposes_only_the_two_selective_targets(self):
        options = self.workflow.split("options:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("- meet-frontend", options)
        self.assertIn("- meet-backend", options)
        self.assertNotIn("- all", options)

    def test_publication_is_develop_only_and_digest_receipted(self):
        self.assertIn("github.ref == 'refs/heads/develop'", self.workflow)
        self.assertIn("push: true", self.workflow)
        self.assertIn("steps.build.outputs.digest", self.workflow)
        self.assertIn("staging_candidate_receipt.py", self.workflow)

    def test_workflow_cannot_apply_a_candidate(self):
        lowered = self.workflow.lower()
        for forbidden in ("kubectl", "helm upgrade", "rollout restart", "kubeconfig"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
