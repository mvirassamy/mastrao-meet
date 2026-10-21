"""Execute the Signal boundary with synthetic data and a fake gh executable."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.test_staging_candidate_workflow import run_scripts, steps


class StagingPublicationSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = Path(
            ".github/workflows/signal-staging-publication.yml"
        ).read_text()
        cls.steps = steps(cls.workflow)
        cls.validation = cls.script(
            "Validate exact completed attempt before issuing a token"
        )
        cls.dispatch = cls.script(
            "Dispatch existing Meet selection workflow on protected main"
        )

    @classmethod
    def script(cls, name):
        return textwrap.dedent(run_scripts(cls.steps[name])[0].split("\n", 1)[1])

    def test_signal_is_local_pinned_and_executes_no_application_code(self):
        self.assertRegex(
            self.workflow,
            r"on:\n  workflow_run:\n    workflows: \[meet Workflow\]\n    types: \[completed\]\n    branches: \[develop\]\n",
        )
        self.assertIn("\npermissions: {}\n", self.workflow)
        self.assertIn("    environment: meet-staging-delivery-signal\n", self.workflow)
        self.assertEqual(len(re.findall(r"(?m)^      - ", self.workflow)), 4)
        for action in re.findall(r"uses: (\S+)", self.workflow):
            self.assertRegex(action, r"^actions/[a-z-]+@[a-f0-9]{40}$")
        for forbidden in [
            "checkout@",
            "download-artifact@",
            "cache@",
            "secrets: inherit",
            "workflow_dispatch:",
            "workflow_call:",
            "contents: write",
            "continue-on-error:",
            "permission-contents:",
            "permission-pull-requests:",
        ]:
            self.assertNotIn(forbidden, self.workflow)
        self.assertEqual(
            re.findall(r"secrets\.([A-Z_]+)", self.workflow),
            ["MEET_DEPLOYMENTS_SIGNAL_APP_PRIVATE_KEY"],
        )
        token = self.steps[
            "Issue existing Signal identity for Deployments Actions only"
        ]
        for expected in [
            "owner: mvirassamy",
            "repositories: mastrao-deployments",
            "permission-actions: write",
        ]:
            self.assertIn(expected, token)
        self.assertNotIn("${{", self.validation + self.dispatch)

    def test_guard_accepts_only_canonical_protected_develop_push(self):
        guard = re.search(
            r"    if: >-\n(.*?)    runs-on:", self.workflow, re.DOTALL
        ).group(1)
        context = {
            "github": {
                "repository": "mvirassamy/mastrao-meet",
                "event_name": "workflow_run",
                "ref": "refs/heads/develop",
                "ref_protected": True,
                "event": {
                    "action": "completed",
                    "workflow_run": {
                        "name": "meet Workflow",
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "develop",
                        "head_repository": {"full_name": "mvirassamy/mastrao-meet"},
                        "event": "push",
                    },
                },
            },
            "vars": {"MEET_DEPLOYMENTS_SIGNAL_ENABLED": "true"},
        }
        rejected = [
            (["vars", "MEET_DEPLOYMENTS_SIGNAL_ENABLED"], ""),
            (["vars", "MEET_DEPLOYMENTS_SIGNAL_ENABLED"], "false"),
            (["github", "repository"], "outside/mastrao-meet"),
            (["github", "event_name"], "workflow_dispatch"),
            (["github", "ref"], "refs/heads/main"),
            (["github", "ref_protected"], False),
            (["github", "event", "action"], "requested"),
            (["github", "event", "workflow_run", "name"], "other Workflow"),
            (["github", "event", "workflow_run", "status"], "in_progress"),
            (["github", "event", "workflow_run", "conclusion"], "failure"),
            (["github", "event", "workflow_run", "conclusion"], "cancelled"),
            (["github", "event", "workflow_run", "head_branch"], "main"),
            (
                ["github", "event", "workflow_run", "head_repository", "full_name"],
                "outside/mastrao-meet",
            ),
            (["github", "event", "workflow_run", "event"], "pull_request"),
            (["github", "event", "workflow_run", "event"], "workflow_dispatch"),
        ]
        evaluator = """
const {runInNewContext} = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const results = [runInNewContext(input.guard, input.context)];
for (const [path, value] of input.rejected) {
  const context = structuredClone(input.context);
  let parent = context;
  for (const key of path.slice(0, -1)) parent = parent[key];
  parent[path.at(-1)] = value;
  results.push(runInNewContext(input.guard, context));
}
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(
            ["node", "-e", evaluator],
            input=json.dumps(
                {"guard": guard, "context": context, "rejected": rejected}
            ),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), [True] + [False] * len(rejected))

    def validate(self, directory, run_id, attempt):
        return subprocess.run(
            ["bash", "-c", self.validation],
            env={
                **os.environ,
                "RUNNER_TEMP": directory,
                "RUN_ID": run_id,
                "RUN_ATTEMPT": attempt,
                "GITHUB_RUN_ID": "456",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SHA": "a" * 40,
            },
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_invalid_ids_fail_before_token_or_dispatch_payload(self):
        for invalid in [
            "",
            "0",
            "01",
            "-1",
            "1.5",
            "9007199254740992",
            "1\n2",
            "$(printf injected)",
            "../main",
        ]:
            for run_id, attempt in [(invalid, "2"), ("123", invalid)]:
                with (
                    self.subTest(run_id=run_id, attempt=attempt),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    result = self.validate(directory, run_id, attempt)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("meet_completed_attempt_invalid", result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(
                        (Path(directory) / "meet-promotion-dispatch.json").exists()
                    )

    def test_dispatch_is_fixed_and_propagates_failure_using_only_fake_gh(self):
        for exit_code in [0, 1]:
            with tempfile.TemporaryDirectory() as directory:
                result = self.validate(directory, "123", "2")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = Path(directory) / "meet-promotion-dispatch.json"
                body = {
                    "ref": "main",
                    "inputs": {
                        "operation": "propose",
                        "run_id": "123",
                        "run_attempt": "2",
                    },
                }
                self.assertEqual(json.loads(payload.read_text()), body)
                capture = Path(directory) / "request.json"
                summary = Path(directory) / "summary.md"
                fake_gh = Path(directory) / "gh"
                fake_gh.write_text(
                    textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    args = sys.argv[1:]
                    body = json.loads(Path(args[args.index('--input') + 1]).read_text())
                    Path(os.environ['SYNTHETIC_CAPTURE']).write_text(json.dumps({{
                        'args': args,
                        'body': body,
                        'tokenMatches': os.environ['GH_TOKEN'] == 'synthetic-signal',
                    }}))
                    sys.exit({exit_code})
                    """)
                )
                fake_gh.chmod(0o700)
                result = subprocess.run(
                    ["bash", "-c", self.dispatch],
                    env={
                        **os.environ,
                        "PATH": directory + os.pathsep + os.environ["PATH"],
                        "RUNNER_TEMP": directory,
                        "GH_TOKEN": "synthetic-signal",
                        "SYNTHETIC_CAPTURE": str(capture),
                        "GITHUB_STEP_SUMMARY": str(summary),
                        "RUN_ID": "123",
                        "RUN_ATTEMPT": "2",
                        "GITHUB_RUN_ID": "456",
                        "GITHUB_RUN_ATTEMPT": "1",
                        "GITHUB_SHA": "a" * 40,
                    },
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(result.stdout, "")
                if exit_code == 0:
                    report = json.loads(
                        summary.read_text().split("```json\n")[1].split("\n```")[0]
                    )
                    self.assertEqual(report["status"], "DISPATCH_ACCEPTED")
                    self.assertEqual(
                        report["source"],
                        {
                            "repository": "mvirassamy/mastrao-meet",
                            "runId": 123,
                            "runAttempt": 2,
                        },
                    )
                    self.assertEqual(
                        report["signal"],
                        {"runId": 456, "runAttempt": 1, "codeSha": "a" * 40},
                    )
                    self.assertIsNone(report["receiver"]["runId"])
                    self.assertFalse(report["deploymentApplied"])
                    self.assertFalse(report["automaticMeetDeliveryProven"])
                else:
                    self.assertFalse(summary.exists())
                request = json.loads(capture.read_text())
                self.assertTrue(request["tokenMatches"])
                self.assertEqual(request["body"], body)
                self.assertEqual(
                    request["args"],
                    [
                        "api",
                        "--method",
                        "POST",
                        "repos/mvirassamy/mastrao-deployments/actions/workflows/prepare-meet-staging.yml/dispatches",
                        "--header",
                        "Accept: application/vnd.github+json",
                        "--header",
                        "X-GitHub-Api-Version: 2022-11-28",
                        "--input",
                        str(payload),
                        "--silent",
                    ],
                )


if __name__ == "__main__":
    unittest.main()
