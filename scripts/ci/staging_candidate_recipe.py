#!/usr/bin/env python3
"""Closed build recipes for the Meet staging candidates.

This module is the single source of the recipe: the workflow renders its
build inputs from it, and the receipt checks the built recipe against it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

OUTPUT_DELIMITER = "MEET_CANDIDATE_BUILD_ARGS"


@dataclass(frozen=True)
class Recipe:
    """How one candidate image is built and where it is pushed."""

    dockerfile: str
    build_target: str
    repository: str
    build_args: tuple[str, ...]


TARGETS = {
    "meet-frontend": Recipe(
        dockerfile="src/frontend/Dockerfile",
        build_target="frontend-production",
        repository="rg.fr-par.scw.cloud/mastrao-staging/meet-frontend",
        build_args=(
            "DOCKER_USER=101:101",
            "VITE_API_BASE_URL=https://meet.mastrao-staging.com",
            "VITE_APP_TITLE=Mastrao Visio",
        ),
    ),
    "meet-backend": Recipe(
        dockerfile="Dockerfile",
        build_target="backend-production",
        repository="rg.fr-par.scw.cloud/mastrao-staging/meet-backend",
        build_args=("DOCKER_USER=10001:10001",),
    ),
}


def recipe_for(target: str) -> Recipe:
    recipe = TARGETS.get(target)
    if recipe is None:
        raise ValueError("unsupported candidate target")
    return recipe


def github_outputs(recipe: Recipe) -> str:
    """Render the recipe as GitHub step outputs."""
    return "".join(
        (
            f"dockerfile={recipe.dockerfile}\n",
            f"build_target={recipe.build_target}\n",
            f"repository={recipe.repository}\n",
            f"build_args<<{OUTPUT_DELIMITER}\n",
            *(f"{build_arg}\n" for build_arg in recipe.build_args),
            f"{OUTPUT_DELIMITER}\n",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    sys.stdout.write(github_outputs(recipe_for(args.target)))


if __name__ == "__main__":
    main()
