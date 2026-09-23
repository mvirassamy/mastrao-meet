#!/usr/bin/env python3
"""Render a closed, secret-free receipt for one published staging candidate."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.ci.staging_candidate_readback import DIGEST, PLATFORM, readback_proof
from scripts.ci.staging_candidate_recipe import Recipe, recipe_for

SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REPOSITORY = "mvirassamy/mastrao-meet"


@dataclass(frozen=True)
class SourceIdentity:
    """The exact Git source a candidate was built from."""

    sha: str
    tree: str
    archive_sha256: str

    def validate(self) -> None:
        if not SHA.fullmatch(self.sha):
            raise ValueError("source SHA must be a lowercase full commit SHA")
        if not SHA.fullmatch(self.tree):
            raise ValueError("source tree must be a lowercase full Git tree SHA")
        if not SHA256.fullmatch(self.archive_sha256):
            raise ValueError("source archive digest must be lowercase sha256 hex")


@dataclass(frozen=True)
class Publication:
    """The pushed index digest and its registry readback proof."""

    digest: str
    readback: object

    def validate(self, repository: str) -> None:
        if not DIGEST.fullmatch(self.digest):
            raise ValueError("candidate digest must be a sha256 OCI digest")
        readback = self.readback
        image_digest = (
            readback.get("imageDigest") if isinstance(readback, dict) else None
        )
        if (
            not isinstance(image_digest, str)
            or not DIGEST.fullmatch(image_digest)
            or readback
            != readback_proof(
                repository=repository,
                index_digest=self.digest,
                image_digest=image_digest,
            )
        ):
            raise ValueError(
                "registry readback is missing or does not match the candidate"
            )


@dataclass(frozen=True)
class WorkflowRun:
    """The GitHub Actions run that produced the candidate."""

    run_id: str
    run_attempt: str

    def validate(self) -> None:
        if not self.run_id.isdecimal() or not self.run_attempt.isdecimal():
            raise ValueError("workflow identity must be numeric")


def build_receipt(
    *,
    target: str,
    source: SourceIdentity,
    built: Recipe,
    publication: Publication,
    run: WorkflowRun,
) -> dict[str, object]:
    if built != recipe_for(target):
        raise ValueError("built recipe does not match the closed target recipe")
    source.validate()
    publication.validate(built.repository)
    run.validate()

    return {
        "schema": "mastrao.meet.staging-candidate-receipt.v1",
        "status": "PUBLISHED_NOT_DEPLOYED",
        "target": target,
        "source": {
            "repository": SOURCE_REPOSITORY,
            "sha": source.sha,
            "tree": source.tree,
            "archiveSha256": source.archive_sha256,
        },
        "recipe": {
            "dockerfile": built.dockerfile,
            "target": built.target,
            "buildArgs": list(built.build_args),
            "platform": PLATFORM,
        },
        "image": {
            "repository": built.repository,
            "digest": publication.digest,
            "reference": f"{built.repository}@{publication.digest}",
        },
        "registryReadback": publication.readback,
        "workflow": {
            "runId": int(run.run_id),
            "runAttempt": int(run.run_attempt),
        },
        "deploymentApplied": False,
    }


def _load_readback(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("registry readback is unreadable") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--source-archive-sha256", required=True)
    parser.add_argument("--dockerfile", required=True)
    parser.add_argument("--build-target", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--build-args", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--readback", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = build_receipt(
        target=args.target,
        source=SourceIdentity(
            sha=args.source_sha,
            tree=args.source_tree,
            archive_sha256=args.source_archive_sha256,
        ),
        built=Recipe(
            dockerfile=args.dockerfile,
            target=args.build_target,
            repository=args.repository,
            build_args=tuple(args.build_args.splitlines()),
        ),
        publication=Publication(
            digest=args.digest, readback=_load_readback(args.readback)
        ),
        run=WorkflowRun(run_id=args.run_id, run_attempt=args.run_attempt),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
