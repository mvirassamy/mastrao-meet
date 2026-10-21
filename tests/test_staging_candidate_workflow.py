import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_recipe import TARGETS

WORKFLOW = Path(".github/workflows/staging-candidate.yml")
CI_WORKFLOW = Path(".github/workflows/meet.yml")
TOOLING_STEPS = (
    "Select the closed build recipe",
    "Verify and bind the local candidate archive",
    "Read back the exact candidate from the registry",
    "Render the immutable candidate receipt",
)


def steps(workflow):
    """Map each step name to its text, up to the next step."""
    parts = re.split(r"^      - name: (.+)\n", workflow, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def run_scripts(workflow):
    """Return every step run: value, one-line or block, with its continuation.

    Handles both `run:` and `- run:`; a bare `run:` mapping such as
    `defaults.run` is not a script and is skipped.
    """
    scripts = []
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*(?:-\s+)?)run:(.*)$", line)
        if match is None or not match.group(2).strip():
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
        targets = re.search(
            r"^        target: \[(.+)\]$", CI_WORKFLOW.read_text(), re.MULTILINE
        )
        self.assertIsNotNone(targets)
        self.assertEqual(
            [target.strip() for target in targets.group(1).split(",")], list(TARGETS)
        )

    def test_publication_job_is_bound_to_develop_and_its_environment(self):
        self.assertRegex(
            self.workflow,
            r"(?m)^  publish-candidate:\n    if: github\.ref == 'refs/heads/develop'\n"
            r"    needs: validate-source\n"
            r"    runs-on: ubuntu-latest\n    environment: staging-candidates\n"
            r"    defaults:\n      run:\n        shell: bash\n",
        )
        self.assertRegex(self.workflow, r"(?m)^permissions:\n  contents: read\n")

    def test_every_step_is_named(self):
        self.assertEqual(
            len(re.findall(r"(?m)^      - ", self.workflow)), len(self.steps)
        )

    def test_steps_cannot_override_the_job_guarantees(self):
        self.assertEqual(
            re.findall(r"(?m)^\s+shell:\s*(.*)$", self.workflow), ["bash", "bash"]
        )
        self.assertEqual(re.findall(r"(?m)^\s+if:", self.workflow), ["    if:"])
        self.assertNotIn("continue-on-error", self.workflow)

    def test_source_guard_accepts_only_authorized_staging_events(self):
        guard = run_scripts(self.steps["Validate authorized staging source"])[0].split(
            "\n", 1
        )[1]
        environment = {
            **os.environ,
            "SOURCE_REPOSITORY": "mvirassamy/mastrao-meet",
            "SOURCE_EVENT_NAME": "push",
            "SOURCE_REF": "refs/heads/develop",
            "SOURCE_REF_PROTECTED": "true",
            "SOURCE_SHA": "a" * 40,
            "TRUSTED_WORKFLOW_SHA": "a" * 40,
        }
        cases = (
            ({}, True),
            ({"SOURCE_EVENT_NAME": "workflow_dispatch"}, True),
            ({"SOURCE_EVENT_NAME": "workflow_dispatch", "SOURCE_SHA": "b" * 40}, False),
            ({"SOURCE_REPOSITORY": "someone/mastrao-meet"}, False),
            ({"SOURCE_EVENT_NAME": "pull_request"}, False),
            ({"SOURCE_REF": "refs/heads/main"}, False),
            ({"SOURCE_REF": "refs/heads/develop_modes"}, False),
            ({"SOURCE_REF_PROTECTED": "false"}, False),
            ({"SOURCE_SHA": ""}, False),
            ({"SOURCE_SHA": "invalid"}, False),
            ({"SOURCE_SHA": "b" * 40}, False),
        )
        for overrides, accepted in cases:
            with self.subTest(overrides=overrides):
                result = subprocess.run(
                    ["bash", "-c", guard],
                    env={**environment, **overrides},
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, accepted)

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
            names.index("Build the selected candidate once"),
        )

    def test_source_is_the_validated_commit_built_in_isolation(self):
        checkout = self.steps["Checkout the selected source commit"]
        self.assertIn("ref: ${{ inputs.source_sha }}", checkout)
        self.assertIn("path: source", checkout)
        self.assertIn("persist-credentials: false", checkout)
        bind = self.steps["Bind the source to develop and record its immutable tree"]
        self.assertIn("working-directory: source", bind)
        for line in (
            'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"',
            'git merge-base --is-ancestor "$SOURCE_SHA" refs/remotes/origin/develop',
            'worktree_status="$(git status --porcelain)"',
            'test -z "$worktree_status"',
        ):
            with self.subTest(line=line):
                self.assertRegex(bind, rf"(?m)^ +{re.escape(line)}$")

    def test_build_inputs_are_exactly_the_closed_recipe(self):
        build = self.steps["Build the selected candidate once"]
        build_inputs = {}
        for line in build.split("        with:\n", 1)[1].splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"          ([\w-]+): (.+)", line)
            self.assertIsNotNone(match, line)
            key, value = match.groups()
            self.assertNotIn(key, build_inputs)
            build_inputs[key] = value
        self.assertEqual(
            build_inputs,
            {
                "context": "source/${{ steps.recipe.outputs.context }}",
                "file": "source/${{ steps.recipe.outputs.dockerfile }}",
                "target": "${{ steps.recipe.outputs.build_target }}",
                "platforms": "linux/amd64",
                "build-args": "${{ steps.recipe.outputs.build_args }}",
                "push": "false",
                "outputs": "type=oci,dest=${{ runner.temp }}/meet-candidate.tar",
                "tags": (
                    "${{ steps.recipe.outputs.repository }}:sha-${{ inputs.source_sha }}"
                    "-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
                ),
                "provenance": "mode=max",
                "sbom": "true",
            },
        )

    def test_archive_is_scanned_before_registry_access_and_published_once(self):
        names = list(self.steps)
        ordered = (
            "Build the selected candidate once",
            "Verify and bind the local candidate archive",
            "Scan the exact candidate archive",
            "Require the dedicated registry publisher",
            "Authenticate to the staging registry",
            "Publish the qualified archive without rebuilding",
            "Read back the exact candidate from the registry",
            "Render the immutable candidate receipt",
        )
        positions = [names.index(name) for name in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.workflow.count("uses: docker/build-push-action@"), 1)
        scan = self.steps["Scan the exact candidate archive"]
        for setting in (
            "input: ${{ runner.temp }}/candidate-layout",
            "scanners: vuln",
            "exit-code: '1'",
            "severity: CRITICAL,HIGH",
        ):
            self.assertIn(setting, scan)

    def test_publication_refuses_changed_archive_before_invoking_oras(self):
        # Exercise the real shell boundary with a harmless ORAS stand-in. No registry.
        script = run_scripts(
            self.steps["Publish the qualified archive without rebuilding"]
        )[0].split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            archive = temporary / "meet-candidate.tar"
            archive.write_bytes(b"qualified bytes")
            checksum = subprocess.run(
                ["sha256sum", str(archive)], capture_output=True, check=True
            ).stdout
            (temporary / "candidate-archive.sha256").write_bytes(checksum)
            call = temporary / "oras-call"
            executable = temporary / "oras"
            executable.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$ORAS_CALL"\n')
            executable.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{temporary}:{os.environ['PATH']}",
                "RUNNER_TEMP": directory,
                "ORAS_CALL": str(call),
                "REPOSITORY": TARGETS["meet-backend"].repository,
                "DIGEST": "sha256:" + "a" * 64,
                "SOURCE_SHA": "b" * 40,
                "RUN_ID": "123",
                "RUN_ATTEMPT": "2",
            }
            result = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", script],
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                call.read_text().splitlines(),
                [
                    "cp",
                    "--from-oci-layout",
                    f"{archive}@sha256:" + "a" * 64,
                    TARGETS["meet-backend"].repository
                    + ":sha-"
                    + "b" * 40
                    + "-run-123-attempt-2",
                ],
            )
            call.unlink()
            archive.write_bytes(b"changed after qualification")
            result = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", script],
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(call.exists())

    def test_candidate_is_read_back_and_receipted_with_its_recipe(self):
        readback = self.steps["Read back the exact candidate from the registry"]
        self.assertIn('imagetools inspect "$REPOSITORY@$DIGEST" --raw', readback)
        self.assertIn("python -m scripts.ci.staging_candidate_readback", readback)
        receipt = self.steps["Render the immutable candidate receipt"]
        self.assertIn("python -m scripts.ci.staging_candidate_receipt", receipt)
        self.assertIn("CONTEXT: ${{ steps.recipe.outputs.context }}", receipt)
        self.assertIn('--context "$CONTEXT"', receipt)
        self.assertIn('--build-args "$BUILD_ARGS"', receipt)

    def test_run_scripts_never_interpolate_expressions(self):
        scripts = run_scripts(self.workflow)
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.splitlines()[0]):
                self.assertNotIn("${{", script)

    def test_every_action_is_pinned_to_a_commit(self):
        uses = re.findall(r"uses:\s*(\S+)", self.workflow)
        self.assertTrue(uses)
        for reference in uses:
            with self.subTest(reference=reference):
                self.assertRegex(
                    reference,
                    r"^(?:[\w.-]+/[\w./-]+@[0-9a-f]{40}"
                    r"|docker://[^@\s]+@sha256:[0-9a-f]{64})$",
                )

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

    def test_agent_subtitles_helm_rendering_runs_in_ci(self):
        workflow = CI_WORKFLOW.read_text()
        ci_contracts = re.search(
            r"(?ms)^  ci-contracts:\n(?P<job>.*?)(?=^  [\w-]+:\n)", workflow
        )
        self.assertIsNotNone(ci_contracts)
        self.assertRegex(
            ci_contracts.group("job"), r"(?m)^        uses: azure/setup-helm@\S+"
        )
        self.assertRegex(
            ci_contracts.group("job"),
            r"(?m)^        run: src/helm/tests/agent-subtitles-image-and-openai-secret\.sh$",
        )

    def test_agent_contract_tests_run_in_ci(self):
        workflow = CI_WORKFLOW.read_text()
        lint_agents = re.search(
            r"(?ms)^  lint-agents:\n(?P<job>.*?)(?=^  [\w-]+:\n)", workflow
        )
        self.assertIsNotNone(lint_agents)
        self.assertIn(
            "run: uv run python -m unittest discover -s tests -v",
            lint_agents.group("job"),
        )


if __name__ == "__main__":
    unittest.main()
