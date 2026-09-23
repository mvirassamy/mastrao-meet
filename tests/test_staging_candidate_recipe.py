import subprocess
import sys
import unittest

from scripts.ci.staging_candidate_recipe import (
    OUTPUT_DELIMITER,
    TARGETS,
    github_outputs,
    recipe_for,
)


class StagingCandidateRecipeTests(unittest.TestCase):
    def test_targets_are_closed_and_fully_specified(self):
        self.assertEqual(set(TARGETS), {"meet-frontend", "meet-backend"})
        for target, recipe in TARGETS.items():
            with self.subTest(target=target):
                self.assertTrue(
                    recipe.repository.startswith("rg.fr-par.scw.cloud/mastrao-staging/")
                )
                self.assertTrue(recipe.build_args)
                for build_arg in recipe.build_args:
                    self.assertRegex(build_arg, r"^[A-Z_]+=\S")
                    self.assertNotEqual(build_arg, OUTPUT_DELIMITER)

    def test_rejects_unknown_targets(self):
        for target in ("all", "", "meet-frontend "):
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(ValueError, "unsupported"),
            ):
                recipe_for(target)

    def test_renders_github_outputs_with_every_build_arg(self):
        recipe = TARGETS["meet-frontend"]
        self.assertEqual(
            github_outputs(recipe).splitlines(),
            [
                "dockerfile=src/frontend/Dockerfile",
                "build_target=frontend-production",
                "repository=rg.fr-par.scw.cloud/mastrao-staging/meet-frontend",
                f"build_args<<{OUTPUT_DELIMITER}",
                "DOCKER_USER=101:101",
                "VITE_API_BASE_URL=https://meet.mastrao-staging.com",
                "VITE_APP_TITLE=Mastrao Visio",
                OUTPUT_DELIMITER,
            ],
        )

    def test_cli_writes_outputs_or_fails_for_unknown_targets(self):
        for target, returncode in (("meet-backend", 0), ("all", 1)):
            with self.subTest(target=target):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.ci.staging_candidate_recipe",
                        "--target",
                        target,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, returncode, result.stderr)
                if returncode == 0:
                    self.assertEqual(result.stdout, github_outputs(TARGETS[target]))
                else:
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
