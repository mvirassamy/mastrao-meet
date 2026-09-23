#!/usr/bin/env python3
"""Reduce an exact remote OCI readback to a closed, secret-free proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

READBACK_SCHEMA = "mastrao.meet.staging-candidate-readback.v1"
PLATFORM = "linux/amd64"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
ATTESTATION_TYPE = "attestation-manifest"
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


def readback_proof(
    *, repository: str, index_digest: str, image_digest: str
) -> dict[str, str]:
    """Return the only readback shape a candidate receipt may embed."""
    return {
        "schema": READBACK_SCHEMA,
        "status": "VERIFIED",
        "repository": repository,
        "digest": index_digest,
        "imageDigest": image_digest,
        "platform": PLATFORM,
    }


def verify_readback(
    *, raw: bytes, repository: str, expected_digest: str
) -> dict[str, str]:
    """Prove that raw is the exact index named by expected_digest.

    The index must hold exactly one linux/amd64 image and an attestation
    manifest that references that image. Any other shape fails closed.
    """
    _require_exact_bytes(raw, expected_digest)
    descriptors = _parse_index_descriptors(raw)
    image_digest = _single_linux_amd64_image(descriptors)
    _require_attestation_for(descriptors, image_digest)
    return readback_proof(
        repository=repository,
        index_digest=expected_digest,
        image_digest=image_digest,
    )


def _require_exact_bytes(raw: bytes, expected_digest: str) -> None:
    if not DIGEST.fullmatch(expected_digest):
        raise ValueError("expected digest must be a sha256 OCI digest")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("remote manifest is empty or too large")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("remote manifest bytes do not match the expected digest")


def _parse_index_descriptors(raw: bytes) -> list[dict[str, object]]:
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("remote manifest is not valid JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") not in INDEX_MEDIA_TYPES
    ):
        raise ValueError("remote manifest schema is unsupported")
    descriptors = manifest.get("manifests")
    if not isinstance(descriptors, list) or not all(
        isinstance(descriptor, dict)
        and isinstance(descriptor.get("digest"), str)
        and DIGEST.fullmatch(descriptor["digest"])
        for descriptor in descriptors
    ):
        raise ValueError("remote index descriptors are malformed")
    return descriptors


def _annotation(descriptor: dict[str, object], key: str) -> object:
    annotations = descriptor.get("annotations")
    return annotations.get(key) if isinstance(annotations, dict) else None


def _is_attestation(descriptor: dict[str, object]) -> bool:
    return _annotation(descriptor, "vnd.docker.reference.type") == ATTESTATION_TYPE


def _single_linux_amd64_image(descriptors: list[dict[str, object]]) -> str:
    images = [d for d in descriptors if not _is_attestation(d)]
    if len(images) != 1:
        raise ValueError("remote index must hold exactly one image manifest")
    platform = images[0].get("platform")
    if not isinstance(platform, dict) or (
        platform.get("os"),
        platform.get("architecture"),
    ) != ("linux", "amd64"):
        raise ValueError("remote index image is not linux/amd64")
    return str(images[0]["digest"])


def _require_attestation_for(
    descriptors: list[dict[str, object]], image_digest: str
) -> None:
    if not any(
        _is_attestation(d)
        and _annotation(d, "vnd.docker.reference.digest") == image_digest
        for d in descriptors
    ):
        raise ValueError("remote index has no attestation for its image")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify_readback(
        raw=args.manifest.read_bytes(),
        repository=args.repository,
        expected_digest=args.expected_digest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
