#!/usr/bin/env python3
"""Render a closed, non-applying OIDC rollout patch for the Meet web API."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from scripts.ci.staging_candidate_readback import DIGEST, readback_proof

PLAN_SCHEMA = "mastrao.meet.staging-oidc-web-plan.v1"
CANDIDATE_SCHEMA = "mastrao.meet.staging-candidate-receipt.v1"
REPOSITORY = "rg.fr-par.scw.cloud/mastrao-staging/meet-backend"
NAMESPACE = "mastrao-staging"
DEPLOYMENT = "meet-api"
CONTAINER = "api"
SECRET_KEY = "OIDC_RP_CLIENT_SECRET"
SHA = re.compile(r"^[0-9a-f]{40}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9._~-]{8,200}$")
SECRET_NAME = re.compile(r"^mastrao-meet-oidc-v[1-9][0-9]*$")
# Any Secret or ConfigMap whose name mentions OIDC is treated as OIDC material,
# whatever its version, so an indirect projection cannot hide behind a new name.
OIDC_SOURCE_NAME = re.compile(r"oidc", re.IGNORECASE)
OIDC_ENV_PREFIX = "OIDC_"

EXCLUDED_CONSUMERS = (
    ("Deployment", "meet-frontend", "frontend"),
    ("Deployment", "worker-general", "worker-general"),
    ("Deployment", "worker-native", "worker-native"),
    ("CronJob", "clean-pending-files", "meet-backend"),
    ("CronJob", "purge-deleted-files", "meet-backend"),
    ("CronJob", "reconcile-mastrao-recordings", "meet-backend"),
)


@dataclass(frozen=True)
class Candidate:
    """An immutable backend candidate with a verified registry readback."""

    source_sha: str
    digest: str
    reference: str


def _require_dict(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be an object")
    return value


def validate_candidate_receipt(receipt: object) -> Candidate:
    """Reduce a candidate receipt to the identity allowed by this rollout."""
    root = _require_dict(receipt, "candidate receipt")
    if root.get("schema") != CANDIDATE_SCHEMA:
        raise ValueError("candidate receipt schema is unsupported")
    if root.get("status") != "PUBLISHED_NOT_DEPLOYED":
        raise ValueError("candidate is not published and unapplied")
    if root.get("target") != "meet-backend":
        raise ValueError("only a meet-backend candidate may update meet-api")
    if root.get("deploymentApplied") is not False:
        raise ValueError("candidate receipt must prove deploymentApplied=false")

    source = _require_dict(root.get("source"), "candidate source")
    source_sha = source.get("sha")
    if not isinstance(source_sha, str) or not SHA.fullmatch(source_sha):
        raise ValueError("candidate source SHA is invalid")

    image = _require_dict(root.get("image"), "candidate image")
    digest = image.get("digest")
    reference = image.get("reference")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError("candidate digest is not immutable")
    if reference != f"{REPOSITORY}@{digest}":
        raise ValueError("candidate image reference is not the closed backend image")
    if image.get("repository") != REPOSITORY:
        raise ValueError("candidate repository is not the staging backend repository")

    readback = _require_dict(
        root.get("registryReadback"), "candidate registry readback"
    )
    image_digest = readback.get("imageDigest")
    if not isinstance(image_digest, str) or not DIGEST.fullmatch(image_digest):
        raise ValueError("candidate registry readback image digest is invalid")
    if readback != readback_proof(
        repository=REPOSITORY,
        index_digest=digest,
        image_digest=image_digest,
    ):
        raise ValueError("candidate registry readback is not fresh and exact")

    return Candidate(source_sha=source_sha, digest=digest, reference=str(reference))


def _resources(document: object) -> Iterator[dict[str, object]]:
    root = _require_dict(document, "workload inventory")
    if root.get("kind") == "List":
        items = root.get("items")
        if not isinstance(items, list):
            raise ValueError("workload List items are malformed")
        for item in items:
            yield from _resources(item)
        return
    yield root


def _identity(resource: dict[str, object]) -> tuple[str, str, str]:
    metadata = _require_dict(resource.get("metadata"), "resource metadata")
    values = (resource.get("kind"), metadata.get("name"), metadata.get("namespace"))
    if not all(isinstance(value, str) for value in values):
        raise ValueError("resource identity is incomplete")
    return str(values[0]), str(values[1]), str(values[2])


def _pod_spec(resource: dict[str, object]) -> dict[str, object]:
    spec = _require_dict(resource.get("spec"), "resource spec")
    if resource.get("kind") == "CronJob":
        job_template = _require_dict(spec.get("jobTemplate"), "CronJob jobTemplate")
        job_spec = _require_dict(job_template.get("spec"), "CronJob job spec")
        template = _require_dict(job_spec.get("template"), "CronJob pod template")
    else:
        template = _require_dict(spec.get("template"), "workload pod template")
    return _require_dict(template.get("spec"), "pod spec")


def _container_list(pod_spec: dict[str, object], field: str) -> list[dict[str, object]]:
    containers = pod_spec.get(field, [] if field == "initContainers" else None)
    if not isinstance(containers, list) or not all(
        isinstance(container, dict) for container in containers
    ):
        raise ValueError(f"workload {field} are malformed")
    return containers


def _containers(resource: dict[str, object]) -> list[dict[str, object]]:
    return _container_list(_pod_spec(resource), "containers")


def _all_containers(resource: dict[str, object]) -> list[dict[str, object]]:
    """Every container of the pod, including init containers and sidecars."""
    pod_spec = _pod_spec(resource)
    return [
        *_container_list(pod_spec, "initContainers"),
        *_container_list(pod_spec, "containers"),
    ]


def _find_consumer(
    resources: list[dict[str, object]], kind: str, name: str, container_name: str
) -> dict[str, object]:
    matches = [
        resource
        for resource in resources
        if _identity(resource) == (kind, name, NAMESPACE)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {kind}/{name} in {NAMESPACE}")
    containers = _containers(matches[0])
    if len([item for item in containers if item.get("name") == container_name]) != 1:
        raise ValueError(
            f"expected exactly one container {container_name} in {kind}/{name}"
        )
    return matches[0]


def _container_env(container: dict[str, object]) -> list[dict[str, object]]:
    env = container.get("env", [])
    if not isinstance(env, list) or not all(isinstance(item, dict) for item in env):
        raise ValueError("container env is malformed")
    return env


def _env_entries(
    resource: dict[str, object], container_name: str
) -> dict[str, dict[str, object]]:
    container = next(
        item for item in _containers(resource) if item.get("name") == container_name
    )
    env = _container_env(container)
    names = [item.get("name") for item in env]
    if not all(isinstance(name, str) for name in names):
        raise ValueError("container env names are malformed")
    normalized = [str(name) for name in names]
    if len(normalized) != len(set(normalized)):
        raise ValueError("container env names must be unique")
    return {str(item["name"]): item for item in env}


def _generated_env_names(env: list[dict[str, object]]) -> set[str]:
    names = [item.get("name") for item in env]
    if not all(isinstance(name, str) for name in names):
        raise ValueError("generated environment names are malformed")
    normalized = [str(name) for name in names]
    if len(normalized) != len(set(normalized)):
        raise ValueError("generated environment names must be unique")
    return set(normalized)


def _is_oidc_source(reference: dict[str, object], key_field: str | None) -> bool:
    name = reference.get("name")
    if isinstance(name, str) and OIDC_SOURCE_NAME.search(name):
        return True
    if key_field is None:
        return False
    key = reference.get(key_field)
    return isinstance(key, str) and key.upper().startswith(OIDC_ENV_PREFIX)


def _container_projects_oidc(container: dict[str, object]) -> bool:
    """Detect direct or indirect OIDC material in one container."""
    for item in _container_env(container):
        name = item.get("name")
        if isinstance(name, str) and name.upper().startswith(OIDC_ENV_PREFIX):
            return True
        value_from = item.get("valueFrom")
        if value_from is None:
            continue
        value_from = _require_dict(value_from, "environment valueFrom")
        for ref_field in ("secretKeyRef", "configMapKeyRef"):
            reference = value_from.get(ref_field)
            if reference is None:
                continue
            if _is_oidc_source(
                _require_dict(reference, f"environment {ref_field}"), "key"
            ):
                return True

    env_from = container.get("envFrom", [])
    if not isinstance(env_from, list) or not all(
        isinstance(item, dict) for item in env_from
    ):
        raise ValueError("container envFrom is malformed")
    for item in env_from:
        prefix = item.get("prefix")
        if isinstance(prefix, str) and prefix.upper().startswith(OIDC_ENV_PREFIX):
            return True
        for ref_field in ("secretRef", "configMapRef"):
            reference = item.get(ref_field)
            if reference is None:
                continue
            if _is_oidc_source(
                _require_dict(reference, f"environment {ref_field}"), None
            ):
                return True
    return False


def _resource_projects_oidc(resource: dict[str, object]) -> bool:
    return any(
        _container_projects_oidc(container) for container in _all_containers(resource)
    )


def validate_workload_scope(
    document: object,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    """Require the exact web target and prove excluded consumers are OIDC-free."""
    resources = list(_resources(document))
    target = _find_consumer(resources, "Deployment", DEPLOYMENT, CONTAINER)
    existing_target_env = _env_entries(target, CONTAINER)
    if _resource_projects_oidc(target):
        raise ValueError("meet-api already contains unmanaged OIDC configuration")

    excluded: list[dict[str, object]] = []
    for kind, name, container in EXCLUDED_CONSUMERS:
        resource = _find_consumer(resources, kind, name, container)
        _env_entries(resource, container)
        if _resource_projects_oidc(resource):
            raise ValueError(
                f"excluded consumer {kind}/{name} contains OIDC configuration"
            )
        excluded.append(
            {
                "kind": kind,
                "name": name,
                "container": container,
                "oidcProjected": False,
            }
        )
    return excluded, existing_target_env


def _public_env(
    client_id: str, platform_origin: str, meet_origin: str
) -> list[dict[str, str]]:
    issuer = f"{platform_origin}/api/auth"
    return [
        {"name": "OIDC_RP_CLIENT_ID", "value": client_id},
        {"name": "OIDC_OP_URL", "value": issuer},
        {"name": "OIDC_OP_JWKS_ENDPOINT", "value": f"{issuer}/jwks"},
        {
            "name": "OIDC_OP_AUTHORIZATION_ENDPOINT",
            "value": f"{issuer}/oauth2/authorize",
        },
        {"name": "OIDC_OP_TOKEN_ENDPOINT", "value": f"{issuer}/oauth2/token"},
        {"name": "OIDC_OP_USER_ENDPOINT", "value": f"{issuer}/oauth2/userinfo"},
        {
            "name": "OIDC_OP_INTROSPECTION_ENDPOINT",
            "value": f"{issuer}/oauth2/introspect",
        },
        {"name": "OIDC_RP_SIGN_ALGO", "value": "RS256"},
        {"name": "OIDC_RP_SCOPES", "value": "openid profile email"},
        {"name": "OIDC_USE_PKCE", "value": "true"},
        {"name": "OIDC_PKCE_CODE_CHALLENGE_METHOD", "value": "S256"},
        {"name": "OIDC_USE_NONCE", "value": "true"},
        {"name": "OIDC_VERIFY_SSL", "value": "true"},
        {"name": "OIDC_STORE_ID_TOKEN", "value": "true"},
        {"name": "OIDC_CREATE_USER", "value": "true"},
        {"name": "OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION", "value": "false"},
        {"name": "OIDC_USER_SUB_FIELD_IMMUTABLE", "value": "true"},
        {"name": "OIDC_REDIRECT_REQUIRE_HTTPS", "value": "true"},
        {"name": "OIDC_REDIRECT_ALLOWED_HOSTS", "value": meet_origin},
        {"name": "LOGIN_REDIRECT_URL", "value": meet_origin},
        {"name": "LOGIN_REDIRECT_URL_FAILURE", "value": meet_origin},
        {"name": "LOGOUT_REDIRECT_URL", "value": meet_origin},
        {"name": "OIDC_AUTH_REQUEST_EXTRA_PARAMS", "value": "{}"},
    ]


def build_plan(
    *,
    receipt: object,
    workloads: object,
    client_id: str,
    secret_name: str,
    platform_origin: str = "https://app.mastrao-staging.com",
    meet_origin: str = "https://meet.mastrao-staging.com",
) -> dict[str, object]:
    """Build a non-applying plan from closed source and workload contracts."""
    if not CLIENT_ID.fullmatch(client_id):
        raise ValueError("OIDC client id is malformed")
    if not SECRET_NAME.fullmatch(secret_name):
        raise ValueError("OIDC secret name must be a versioned Meet OIDC secret")
    if platform_origin != "https://app.mastrao-staging.com":
        raise ValueError("Platform origin is outside the staging contract")
    if meet_origin != "https://meet.mastrao-staging.com":
        raise ValueError("Meet origin is outside the staging contract")

    candidate = validate_candidate_receipt(receipt)
    excluded, existing_target_env = validate_workload_scope(workloads)
    env: list[dict[str, object]] = [
        *_public_env(client_id, platform_origin, meet_origin),
        {
            "name": SECRET_KEY,
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": SECRET_KEY}},
        },
    ]
    generated_env = _generated_env_names(env)
    # Strategic merge replaces same-named entries. A managed name that already
    # exists (e.g. LOGIN_REDIRECT_URL from Helm) must be identical, otherwise the
    # plan would silently change a non-OIDC variable. Only names are reported.
    conflicts = sorted(
        str(item["name"])
        for item in env
        if str(item["name"]) in existing_target_env
        and existing_target_env[str(item["name"])] != item
    )
    if conflicts:
        raise ValueError(
            "meet-api already defines managed environment with a different value: "
            + ", ".join(conflicts)
        )
    patch = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": DEPLOYMENT, "namespace": NAMESPACE},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "mastrao.com/meet-auth-candidate-digest": candidate.digest,
                        "mastrao.com/meet-auth-source-sha": candidate.source_sha,
                        "mastrao.com/meet-auth-secret-ref": secret_name,
                    }
                },
                "spec": {
                    "containers": [
                        {"name": CONTAINER, "image": candidate.reference, "env": env}
                    ]
                },
            }
        },
    }
    return {
        "schema": PLAN_SCHEMA,
        "status": "PLANNED_NOT_APPLIED",
        "applyAuthorized": False,
        "candidateDigest": candidate.digest,
        "freshReadback": True,
        "target": {
            "kind": "Deployment",
            "namespace": NAMESPACE,
            "name": DEPLOYMENT,
            "container": CONTAINER,
        },
        "excludedConsumers": excluded,
        "patchContract": {
            "type": "strategicMergePatch",
            "containerMergeKey": "name",
            "environmentMergeKey": "name",
            "preserveExistingEnvironment": True,
            "serverDryRunRequired": True,
        },
        "preservationRequirement": {
            "observedExistingEnvironmentNames": sorted(existing_target_env),
            "requiredAfterDryRunEnvironmentNames": sorted(
                set(existing_target_env) | generated_env
            ),
        },
        "strategicMergePatch": patch,
    }


def _load_json(path: Path, description: str) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{description} is unreadable") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-receipt", required=True, type=Path)
    parser.add_argument("--workloads", required=True, type=Path)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = build_plan(
        receipt=_load_json(args.candidate_receipt, "candidate receipt"),
        workloads=_load_json(args.workloads, "workload inventory"),
        client_id=args.client_id,
        secret_name=args.secret_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
