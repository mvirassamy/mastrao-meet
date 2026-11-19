import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def package_commands(dockerfile, stage):
    source = Path(dockerfile).read_text()
    source = source.split(f" AS {stage}\n", 1)[1].split("\nFROM ", 1)[0]
    source = source.replace("\\\n", "")
    install = next(line[4:] for line in source.splitlines() if line.startswith("RUN "))
    return " && ".join(
        command.strip()
        for command in install.split("&&")
        if command.strip().split()[0] in {"apk", "apt-get"}
    )


def execute_package_shell(dockerfile, stage):
    """Exercise the real shell arguments without installing or cleaning files."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        binary = root / "bin"
        binary.mkdir()
        recorder = (
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['PACKAGE_CALLS'], 'a') as output:\n"
            "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        )
        for manager in ("apk", "apt-get"):
            command = binary / manager
            command.write_text(recorder)
            command.chmod(0o755)
        calls = root / "calls.jsonl"
        result = subprocess.run(
            ["/bin/sh", "-eu", "-c", package_commands(dockerfile, stage)],
            cwd=root,
            env={
                **os.environ,
                "PATH": f"{binary}:{os.environ['PATH']}",
                "PACKAGE_CALLS": str(calls),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return (
            [json.loads(line) for line in calls.read_text().splitlines()],
            sorted(path.name for path in root.iterdir()),
            result.stdout,
        )


class RuntimeImagePackagesTests(unittest.TestCase):
    def test_frontend_updates_all_installed_packages_without_shell_redirection(self):
        calls, files, output = execute_package_shell(
            "src/frontend/Dockerfile", "frontend-production"
        )
        upgrade = next(call for call in calls if "upgrade" in call)
        packages = [
            arg for arg in upgrade if arg != "upgrade" and not arg.startswith("-")
        ]
        self.assertEqual(
            packages, [], "A package filter leaves other runtime CVEs intact"
        )
        self.assertEqual(files, ["bin", "calls.jsonl"])
        self.assertEqual(output, "")

    def test_agents_install_from_the_base_image_snapshot_without_global_upgrade(self):
        calls, files, output = execute_package_shell("src/agents/Dockerfile", "base")
        actions = [call[0] for call in calls]
        self.assertEqual(actions, ["update", "install"])
        self.assertIn("--no-install-recommends", calls[1])
        self.assertEqual(files, ["bin", "calls.jsonl"])
        self.assertEqual(output, "")

    def test_agents_build_removes_timestamped_apt_state(self):
        dockerfile = Path("src/agents/Dockerfile").read_text()
        base = dockerfile.split(" AS base\n", 1)[1].split("\nFROM ", 1)[0]

        self.assertIn("ARG SOURCE_DATE_EPOCH", base)
        self.assertIn(
            "https://snapshot.debian.org/archive/debian/20260803T000000Z",
            base,
        )
        self.assertIn(
            "https://snapshot.debian.org/archive/debian-security/20260803T000000Z",
            base,
        )
        self.assertIn("Check-Valid-Until: no", base)
        self.assertNotIn("apt-get upgrade", base)
        for path in (
            "/var/lib/apt/lists",
            "/var/cache/apt",
            "/var/log/apt",
            "/var/cache/ldconfig",
        ):
            self.assertIn(path, base)
        self.assertIn("alternatives.log", base)
        self.assertIn("dpkg.log", base)


if __name__ == "__main__":
    unittest.main()
