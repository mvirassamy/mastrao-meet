#!/usr/bin/env python3
"""Render a closed, secret-free receipt for one published staging candidate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
    *,
    target: str,
    source_sha: str,
    source_tree: str,
    source_archive_sha256: str,
    digest: str,
    readback: object,
    run_id: str,
    run_attempt: str,
):
    if target not in TARGETS:
        raise ValueError("unsupported candidate target")
    if not SHA.fullmatch(source_sha):
        raise ValueError("source SHA must be a lowercase full commit SHA")
    if not SHA.fullmatch(source_tree):
        raise ValueError("source tree must be a lowercase full Git tree SHA")
    if not SHA256.fullmatch(source_archive_sha256):
        raise ValueError("source archive digest must be lowercase sha256 hex")
    if not DIGEST.fullmatch(digest):
        raise ValueError("candidate digest must be a sha256 OCI digest")
    if readback != {
        "schema": "mastrao.meet.staging-candidate-readback.v1",
        "status": "VERIFIED",
        "digest": digest,
        "platform": "linux/amd64",
    }:
        raise ValueError("registry readback is missing or does not match the candidate")
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
            "tree": source_tree,
            "archiveSha256": source_archive_sha256,
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
        "registryReadback": readback,
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
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--source-archive-sha256", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--readback", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = build_receipt(
        target=args.target,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        source_archive_sha256=args.source_archive_sha256,
        digest=args.digest,
        readback=json.loads(args.readback.read_text()),
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
