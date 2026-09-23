#!/usr/bin/env python3
"""Render a closed, secret-free receipt for one published staging candidate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TARGETS = {
    "meet-backend": {
        "dockerfile": "Dockerfile",
        "target": "backend-production",
        "repository": "rg.fr-par.scw.cloud/mastrao-staging/meet-backend",
    },
    "meet-frontend": {
        "dockerfile": "src/frontend/Dockerfile",
        "target": "frontend-production",
        "repository": "rg.fr-par.scw.cloud/mastrao-staging/meet-frontend",
    },
}


def build_receipt(
    *, target: str, source_sha: str, digest: str, run_id: str, run_attempt: str
):
    if target not in TARGETS:
        raise ValueError("unsupported candidate target")
    if not SHA.fullmatch(source_sha):
        raise ValueError("source SHA must be a lowercase full commit SHA")
    if not DIGEST.fullmatch(digest):
        raise ValueError("candidate digest must be a sha256 OCI digest")
    if not run_id.isdecimal() or not run_attempt.isdecimal():
        raise ValueError("workflow identity must be numeric")

    recipe = TARGETS[target]
    repository = recipe["repository"]
    return {
        "schema": "mastrao.meet.staging-candidate-receipt.v1",
        "status": "PUBLISHED_NOT_DEPLOYED",
        "target": target,
        "source": {
            "repository": "mvirassamy/mastrao-meet",
            "sha": source_sha,
        },
        "recipe": {
            "dockerfile": recipe["dockerfile"],
            "target": recipe["target"],
            "platform": "linux/amd64",
        },
        "image": {
            "repository": repository,
            "digest": digest,
            "reference": f"{repository}@{digest}",
        },
        "workflow": {
            "runId": int(run_id),
            "runAttempt": int(run_attempt),
        },
        "deploymentApplied": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = build_receipt(
        target=args.target,
        source_sha=args.source_sha,
        digest=args.digest,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
