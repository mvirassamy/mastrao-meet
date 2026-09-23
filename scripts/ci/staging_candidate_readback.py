#!/usr/bin/env python3
"""Reduce an exact remote OCI readback to a closed, secret-free proof."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
ATTESTATION_TYPE = "attestation-manifest"
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


def verify_readback(*, raw: bytes, expected_digest: str) -> dict[str, object]:
    if not DIGEST.fullmatch(expected_digest):
        raise ValueError("expected digest must be a sha256 OCI digest")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("remote manifest is empty or too large")
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
    if not isinstance(descriptors, list):
        raise TypeError("remote manifest is not an OCI image index")

    linux_amd64 = False
    attestation = False
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            continue
        descriptor_digest = descriptor.get("digest")
        if not isinstance(descriptor_digest, str) or not DIGEST.fullmatch(
            descriptor_digest
        ):
            continue
        platform = descriptor.get("platform")
        if isinstance(platform, dict):
            linux_amd64 |= (
                platform.get("os") == "linux"
                and platform.get("architecture") == "amd64"
            )
        annotations = descriptor.get("annotations")
        if isinstance(annotations, dict):
            attestation |= (
                annotations.get("vnd.docker.reference.type") == ATTESTATION_TYPE
            )
    if not linux_amd64:
        raise ValueError("remote index has no linux/amd64 image")
    if not attestation:
        raise ValueError("remote index has no attestation manifest")
    return {
        "schema": "mastrao.meet.staging-candidate-readback.v1",
        "status": "VERIFIED",
        "digest": expected_digest,
        "platform": "linux/amd64",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    result = verify_readback(raw=raw, expected_digest=args.expected_digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
