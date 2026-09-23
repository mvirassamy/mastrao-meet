import re
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_receipt import TARGETS

WORKFLOW = Path(".github/workflows/staging-candidate.yml")
CI_WORKFLOW = Path(".github/workflows/meet.yml")


def step(workflow, name):
    """Return the text of one named step, up to the next step."""
    match = re.search(
        rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing workflow step: {name}")
    return match.group(1)


class StagingCandidateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_exposes_exactly_the_two_selective_targets(self):
        options = re.search(
            r"^        options:\n((?:          - .+\n)+)", self.workflow, re.MULTILINE
        )
        self.assertIsNotNone(options)
        self.assertEqual(
            re.findall(r"- (\S+)", options.group(1)), ["meet-frontend", "meet-backend"]
        )
        self.assertEqual(set(TARGETS), {"meet-frontend", "meet-backend"})

    def test_publication_job_is_bound_to_develop_and_its_environment(self):
        self.assertRegex(
            self.workflow,
            r"(?m)^  publish-candidate:\n    if: github\.ref == 'refs/heads/develop'\n"
            r"    runs-on: ubuntu-latest\n    environment: staging-candidates\n",
        )
        self.assertRegex(self.workflow, r"(?m)^permissions:\n  contents: read\n")

    def test_source_is_the_validated_commit_and_is_digest_receipted(self):
        self.assertIn("ref: ${{ inputs.source_sha }}", self.workflow)
        bind = step(
            self.workflow, "Bind the source to develop and record its immutable tree"
        )
        self.assertIn('git merge-base --is-ancestor "$SOURCE_SHA"', bind)
        self.assertIn('test "$(git rev-parse HEAD)" = "$SOURCE_SHA"', bind)
        readback = step(
            self.workflow, "Read back the exact candidate from the registry"
        )
        self.assertIn('imagetools inspect "$REPOSITORY@$DIGEST" --raw', readback)
        self.assertIn("python -m scripts.ci.staging_candidate_readback", readback)
        receipt = step(self.workflow, "Render the immutable candidate receipt")
        self.assertIn("python -m scripts.ci.staging_candidate_receipt", receipt)

    def test_build_recipes_match_the_receipt_targets(self):
        recipe = step(self.workflow, "Select the closed build recipe")
        for target, expected in TARGETS.items():
            with self.subTest(target=target):
                block = re.search(
                    rf"^            {re.escape(target)}\)\n(.*?)^              ;;",
                    recipe,
                    re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(block)
                outputs = dict(re.findall(r"echo '(\w+)=([^']+)'", block.group(1)))
                self.assertEqual(
                    {
                        "dockerfile": outputs.get("dockerfile"),
                        "target": outputs.get("build_target"),
                        "repository": outputs.get("repository"),
                    },
                    expected,
                )

    def test_build_uses_the_input_sha_never_the_dispatch_sha(self):
        build = step(self.workflow, "Build and publish the selected candidate")
        self.assertIn("inputs.source_sha", build)
        self.assertNotIn("github.sha", build)
        self.assertIn("push: true", build)
        self.assertIn("platforms: linux/amd64", build)

    def test_run_scripts_never_interpolate_expressions(self):
        for block in re.findall(
            r"^        run: \|\n((?:          .*\n|\n)+)", self.workflow, re.MULTILINE
        ):
            with self.subTest(block=block.splitlines()[0]):
                self.assertNotIn("${{", block)

    def test_secret_handling_actions_are_pinned_to_commits(self):
        uses = re.findall(r"uses: (docker/[\w-]+)@(\S+)", self.workflow)
        self.assertEqual(len(uses), 3)
        for action, ref in uses:
            with self.subTest(action=action):
                self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_workflow_cannot_apply_a_candidate(self):
        lowered = self.workflow.lower()
        for forbidden in ("kubectl", "helm", "rollout restart", "kubeconfig"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_root_contract_tests_run_in_ci(self):
        self.assertIn(
            "run: python -m unittest discover -s tests -t . -v", CI_WORKFLOW.read_text()
        )


if __name__ == "__main__":
    unittest.main()
