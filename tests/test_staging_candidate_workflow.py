import re
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_recipe import TARGETS

WORKFLOW = Path(".github/workflows/staging-candidate.yml")
CI_WORKFLOW = Path(".github/workflows/meet.yml")
TOOLING_STEPS = (
    "Select the closed build recipe",
    "Read back the exact candidate from the registry",
    "Render the immutable candidate receipt",
)


def steps(workflow):
    """Map each step name to its text, up to the next step."""
    parts = re.split(r"^      - name: (.+)\n", workflow, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def run_scripts(workflow):
    """Return every run: value, one-line or block, with its continuation."""
    scripts = []
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:(.*)$", line)
        if match is None:
            continue
        indent = len(match.group(1))
        script = [match.group(2)]
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            script.append(following)
        scripts.append("\n".join(script))
    return scripts


class StagingCandidateWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()
        cls.steps = steps(cls.workflow)

    def test_exposes_exactly_the_closed_targets(self):
        options = re.search(
            r"^        options:\n((?:          - .+\n)+)", self.workflow, re.MULTILINE
        )
        self.assertIsNotNone(options)
        self.assertEqual(re.findall(r"- (\S+)", options.group(1)), list(TARGETS))

    def test_publication_job_is_bound_to_develop_and_its_environment(self):
        self.assertRegex(
            self.workflow,
            r"(?m)^  publish-candidate:\n    if: github\.ref == 'refs/heads/develop'\n"
            r"    runs-on: ubuntu-latest\n    environment: staging-candidates\n",
        )
        self.assertRegex(self.workflow, r"(?m)^permissions:\n  contents: read\n")

    def test_tooling_comes_from_the_trusted_workflow_commit(self):
        checkout = self.steps["Checkout the trusted candidate tooling"]
        self.assertIn("ref: ${{ github.sha }}", checkout)
        self.assertIn("path: ci-tools", checkout)
        self.assertIn("persist-credentials: false", checkout)
        for name in TOOLING_STEPS:
            with self.subTest(step=name):
                self.assertIn("working-directory: ci-tools", self.steps[name])

    def test_recipe_and_tooling_are_resolved_before_any_push(self):
        names = list(self.steps)
        recipe = self.steps["Select the closed build recipe"]
        self.assertIn("python -m scripts.ci.staging_candidate_recipe", recipe)
        self.assertIn("import scripts.ci.staging_candidate_readback", recipe)
        self.assertIn("scripts.ci.staging_candidate_receipt", recipe)
        self.assertLess(
            names.index("Select the closed build recipe"),
            names.index("Build and publish the selected candidate"),
        )

    def test_source_is_the_validated_commit_built_in_isolation(self):
        checkout = self.steps["Checkout the selected source commit"]
        self.assertIn("ref: ${{ inputs.source_sha }}", checkout)
        self.assertIn("path: source", checkout)
        self.assertIn("persist-credentials: false", checkout)
        bind = self.steps["Bind the source to develop and record its immutable tree"]
        self.assertIn("working-directory: source", bind)
        self.assertIn('git merge-base --is-ancestor "$SOURCE_SHA"', bind)
        self.assertIn('test "$(git rev-parse HEAD)" = "$SOURCE_SHA"', bind)
        build = self.steps["Build and publish the selected candidate"]
        self.assertIn("context: source\n", build)
        self.assertIn("file: source/${{ steps.recipe.outputs.dockerfile }}", build)
        self.assertIn("build-args: ${{ steps.recipe.outputs.build_args }}", build)
        self.assertIn("platforms: linux/amd64", build)
        self.assertNotIn("github.sha", build)

    def test_candidate_is_read_back_and_receipted_with_its_recipe(self):
        readback = self.steps["Read back the exact candidate from the registry"]
        self.assertIn('imagetools inspect "$REPOSITORY@$DIGEST" --raw', readback)
        self.assertIn("python -m scripts.ci.staging_candidate_readback", readback)
        receipt = self.steps["Render the immutable candidate receipt"]
        self.assertIn("python -m scripts.ci.staging_candidate_receipt", receipt)
        self.assertIn('--build-args "$BUILD_ARGS"', receipt)

    def test_run_scripts_never_interpolate_expressions(self):
        scripts = run_scripts(self.workflow)
        self.assertEqual(len(scripts), 6)
        for script in scripts:
            with self.subTest(script=script.splitlines()[0]):
                self.assertNotIn("${{", script)

    def test_every_action_is_pinned_to_a_commit(self):
        uses = re.findall(r"uses: ([\w./-]+)@(\S+)", self.workflow)
        self.assertEqual(len(uses), 6)
        for action, ref in uses:
            with self.subTest(action=action):
                self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_workflow_cannot_apply_a_candidate(self):
        lowered = self.workflow.lower()
        for forbidden in ("kubectl", "helm", "rollout restart", "kubeconfig"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_root_contract_tests_run_in_ci(self):
        self.assertRegex(
            CI_WORKFLOW.read_text(),
            r"(?m)^  ci-contracts:\n(?:    .*\n|\n)*?"
            r"        run: python -m unittest discover -s tests -t \. -v\n",
        )


if __name__ == "__main__":
    unittest.main()
