"""Keep the source hook unprivileged and pinned to trusted orchestration."""

import re
import unittest
from pathlib import Path


class StagingPublicationSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = Path(
            ".github/workflows/signal-staging-publication.yml"
        ).read_text()

    def test_completion_hook_is_guarded_and_calls_one_immutable_workflow(self):
        self.assertRegex(
            self.workflow,
            r"on:\n  workflow_run:\n    workflows: \[meet Workflow\]\n"
            r"    types: \[completed\]\n    branches: \[develop\]\n",
        )
        self.assertIn(
            "    if: vars.MEET_DEPLOYMENTS_SIGNAL_ENABLED == 'true'\n",
            self.workflow,
        )
        calls = re.findall(r"(?m)^    uses: (.+)$", self.workflow)
        self.assertEqual(len(calls), 1)
        self.assertRegex(
            calls[0],
            r"^mvirassamy/mastrao-deployments/\.github/workflows/"
            r"signal-meet-publication\.yml@[a-f0-9]{40}$",
        )

    def test_source_runs_no_privileged_steps_and_passes_only_the_signal_key(self):
        self.assertIn("\npermissions: {}\n", self.workflow)
        for forbidden in (
            "steps:",
            "runs-on:",
            "workflow_dispatch:",
            "pull_request_target:",
            "secrets: inherit",
            "contents: write",
            "with:",
            "environment:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.workflow)
        self.assertEqual(
            re.findall(r"secrets\.([A-Z_]+)", self.workflow),
            ["MEET_DEPLOYMENTS_SIGNAL_APP_PRIVATE_KEY"],
        )


if __name__ == "__main__":
    unittest.main()
