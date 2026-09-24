import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.ci.staging_candidate_readback import readback_proof
from scripts.ci.staging_oidc_web_plan import (
    EXCLUDED_CONSUMERS,
    REPOSITORY,
    SECRET_KEY,
    build_plan,
)

DIGEST = "sha256:" + "a" * 64
IMAGE_DIGEST = "sha256:" + "b" * 64
SOURCE_SHA = "c" * 40
CLIENT_ID = "meet-client-0123456789"
SECRET_NAME = "mastrao-meet-oidc-v1"


def candidate_receipt():
    return {
        "schema": "mastrao.meet.staging-candidate-receipt.v1",
        "status": "PUBLISHED_NOT_DEPLOYED",
        "target": "meet-backend",
        "source": {"sha": SOURCE_SHA},
        "image": {
            "repository": REPOSITORY,
            "digest": DIGEST,
            "reference": f"{REPOSITORY}@{DIGEST}",
        },
        "registryReadback": readback_proof(
            repository=REPOSITORY,
            index_digest=DIGEST,
            image_digest=IMAGE_DIGEST,
        ),
        "deploymentApplied": False,
    }


def deployment(name, container):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "mastrao-staging"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": container,
                            "image": "example.invalid/unchanged:tag",
                            "env": [],
                        }
                    ]
                }
            }
        },
    }


def cronjob(name, container):
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": name, "namespace": "mastrao-staging"},
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": container,
                                    "image": "example.invalid/unchanged:tag",
                                    "env": [],
                                }
                            ]
                        }
                    }
                }
            }
        },
    }


def workload_inventory():
    items = [deployment("meet-api", "api")]
    for kind, name, container in EXCLUDED_CONSUMERS:
        items.append(
            deployment(name, container)
            if kind == "Deployment"
            else cronjob(name, container)
        )
    return {"apiVersion": "v1", "kind": "List", "items": items}


def container_for(resource):
    if resource["kind"] == "CronJob":
        return resource["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
    return resource["spec"]["template"]["spec"]["containers"][0]


class StagingOidcWebPlanTests(unittest.TestCase):
    def test_plan_targets_only_meet_api_with_digest_and_secret_reference(self):
        plan = build_plan(
            receipt=candidate_receipt(),
            workloads=workload_inventory(),
            client_id=CLIENT_ID,
            secret_name=SECRET_NAME,
        )

        self.assertEqual(plan["status"], "PLANNED_NOT_APPLIED")
        self.assertIs(plan["applyAuthorized"], False)
        self.assertIs(plan["freshReadback"], True)
        self.assertEqual(plan["target"]["name"], "meet-api")
        patch = plan["strategicMergePatch"]
        self.assertEqual(
            patch["metadata"],
            {"name": "meet-api", "namespace": "mastrao-staging"},
        )
        patched_container = patch["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(patched_container["name"], "api")
        self.assertEqual(patched_container["image"], f"{REPOSITORY}@{DIGEST}")
        env_names = [item["name"] for item in patched_container["env"]]
        self.assertEqual(len(env_names), len(set(env_names)))
        self.assertEqual(env_names.count("OIDC_REDIRECT_ALLOWED_HOSTS"), 1)
        self.assertNotIn("$patch", json.dumps(patch))
        self.assertEqual(
            plan["patchContract"],
            {
                "type": "strategicMergePatch",
                "containerMergeKey": "name",
                "environmentMergeKey": "name",
                "preserveExistingEnvironment": True,
                "serverDryRunRequired": True,
            },
        )

        secret_env = next(
            item for item in patched_container["env"] if item["name"] == SECRET_KEY
        )
        self.assertNotIn("value", secret_env)
        self.assertEqual(
            secret_env["valueFrom"]["secretKeyRef"],
            {"name": SECRET_NAME, "key": SECRET_KEY},
        )
        self.assertEqual(
            {(item["kind"], item["name"]) for item in plan["excludedConsumers"]},
            {(kind, name) for kind, name, _ in EXCLUDED_CONSUMERS},
        )
        self.assertTrue(
            all(not item["oidcProjected"] for item in plan["excludedConsumers"])
        )

    def test_preserves_existing_non_oidc_environment_by_contract(self):
        inventory = workload_inventory()
        container_for(inventory["items"][0])["env"] = [
            {"name": "DJANGO_ALLOWED_HOSTS", "value": "meet.example"},
            {"name": "LOG_LEVEL", "value": "INFO"},
        ]

        plan = build_plan(
            receipt=candidate_receipt(),
            workloads=inventory,
            client_id=CLIENT_ID,
            secret_name=SECRET_NAME,
        )

        requirement = plan["preservationRequirement"]
        self.assertEqual(
            requirement["observedExistingEnvironmentNames"],
            ["DJANGO_ALLOWED_HOSTS", "LOG_LEVEL"],
        )
        self.assertTrue(
            {"DJANGO_ALLOWED_HOSTS", "LOG_LEVEL"}.issubset(
                requirement["requiredAfterDryRunEnvironmentNames"]
            )
        )

    def test_refuses_duplicate_existing_environment_names(self):
        inventory = workload_inventory()
        container_for(inventory["items"][0])["env"] = [
            {"name": "LOG_LEVEL", "value": "INFO"},
            {"name": "LOG_LEVEL", "value": "DEBUG"},
        ]

        with self.assertRaisesRegex(ValueError, "env names must be unique"):
            build_plan(
                receipt=candidate_receipt(),
                workloads=inventory,
                client_id=CLIENT_ID,
                secret_name=SECRET_NAME,
            )

    def test_refuses_oidc_projection_in_every_excluded_consumer(self):
        for index, (kind, name, _) in enumerate(EXCLUDED_CONSUMERS, start=1):
            with self.subTest(kind=kind, name=name):
                inventory = workload_inventory()
                container_for(inventory["items"][index])["env"] = [
                    {
                        "name": SECRET_KEY,
                        "valueFrom": {
                            "secretKeyRef": {"name": SECRET_NAME, "key": SECRET_KEY}
                        },
                    }
                ]
                with self.assertRaisesRegex(ValueError, "excluded consumer"):
                    build_plan(
                        receipt=candidate_receipt(),
                        workloads=inventory,
                        client_id=CLIENT_ID,
                        secret_name=SECRET_NAME,
                    )

    def test_refuses_indirect_secret_projection_in_excluded_consumers(self):
        aliased_key = workload_inventory()
        container_for(aliased_key["items"][1])["env"] = [
            {
                "name": "RENAMED_SECRET",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "another-secret",
                        "key": SECRET_KEY,
                    }
                },
            }
        ]
        whole_secret = workload_inventory()
        container_for(whole_secret["items"][4])["envFrom"] = [
            {"secretRef": {"name": SECRET_NAME}}
        ]

        for inventory in (aliased_key, whole_secret):
            with (
                self.subTest(inventory=inventory),
                self.assertRaisesRegex(ValueError, "excluded consumer"),
            ):
                build_plan(
                    receipt=candidate_receipt(),
                    workloads=inventory,
                    client_id=CLIENT_ID,
                    secret_name=SECRET_NAME,
                )

    def test_refuses_unmanaged_oidc_state_or_an_incomplete_inventory(self):
        configured = workload_inventory()
        container_for(configured["items"][0])["env"] = [
            {"name": "OIDC_RP_CLIENT_ID", "value": CLIENT_ID}
        ]
        missing_worker = workload_inventory()
        missing_worker["items"] = [
            item
            for item in missing_worker["items"]
            if item["metadata"]["name"] != "worker-native"
        ]
        for inventory, message in (
            (configured, "already contains unmanaged OIDC"),
            (missing_worker, "expected exactly one Deployment/worker-native"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_plan(
                    receipt=candidate_receipt(),
                    workloads=inventory,
                    client_id=CLIENT_ID,
                    secret_name=SECRET_NAME,
                )

    def test_refuses_mutable_wrong_or_already_applied_candidates(self):
        cases = []
        frontend = candidate_receipt()
        frontend["target"] = "meet-frontend"
        cases.append((frontend, "only a meet-backend"))
        mutable = candidate_receipt()
        mutable["image"]["digest"] = "latest"
        cases.append((mutable, "digest is not immutable"))
        drifted = candidate_receipt()
        drifted["registryReadback"]["status"] = "FAILED"
        cases.append((drifted, "readback is not fresh and exact"))
        applied = candidate_receipt()
        applied["deploymentApplied"] = True
        cases.append((applied, "deploymentApplied=false"))

        for receipt, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_plan(
                    receipt=receipt,
                    workloads=workload_inventory(),
                    client_id=CLIENT_ID,
                    secret_name=SECRET_NAME,
                )

    def test_refuses_open_identifiers_and_origins(self):
        cases = (
            ({"client_id": "short"}, "client id"),
            ({"secret_name": "backend"}, "versioned Meet OIDC secret"),
            ({"platform_origin": "https://attacker.example"}, "Platform origin"),
            ({"meet_origin": "http://meet.mastrao-staging.com"}, "Meet origin"),
        )
        defaults = {
            "receipt": candidate_receipt(),
            "workloads": workload_inventory(),
            "client_id": CLIENT_ID,
            "secret_name": SECRET_NAME,
        }
        for override, message in cases:
            with (
                self.subTest(override=override),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_plan(**{**defaults, **override})

    def test_cli_writes_a_secret_free_non_applying_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "candidate.json"
            workloads = root / "workloads.json"
            output = root / "plan.json"
            receipt.write_text(json.dumps(candidate_receipt()))
            workloads.write_text(json.dumps(workload_inventory()))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.ci.staging_oidc_web_plan",
                    "--candidate-receipt",
                    str(receipt),
                    "--workloads",
                    str(workloads),
                    "--client-id",
                    CLIENT_ID,
                    "--secret-name",
                    SECRET_NAME,
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text())
            self.assertIs(plan["applyAuthorized"], False)
            self.assertNotIn("clientSecret", json.dumps(plan))
            self.assertNotIn("secretValue", json.dumps(plan))

    def _pod_spec_for(self, resource):
        if resource["kind"] == "CronJob":
            return resource["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        return resource["spec"]["template"]["spec"]

    def _build(self, inventory):
        return build_plan(
            receipt=candidate_receipt(),
            workloads=inventory,
            client_id=CLIENT_ID,
            secret_name=SECRET_NAME,
        )

    def test_refuses_oidc_hidden_in_init_sidecar_or_config_map(self):
        oidc_env = {"name": "OIDC_RP_CLIENT_ID", "value": CLIENT_ID}
        cases = {}

        init_container = workload_inventory()
        self._pod_spec_for(init_container["items"][2])["initContainers"] = [
            {"name": "migrate", "image": "example.invalid/x:1", "env": [oidc_env]}
        ]
        cases["init container"] = init_container

        sidecar = workload_inventory()
        self._pod_spec_for(sidecar["items"][4])["containers"].append(
            {
                "name": "sidecar",
                "image": "example.invalid/x:1",
                "envFrom": [{"configMapRef": {"name": "meet-oidc-settings"}}],
            }
        )
        cases["sidecar configMapRef"] = sidecar

        config_map_key = workload_inventory()
        container_for(config_map_key["items"][1])["env"] = [
            {
                "name": "RENAMED",
                "valueFrom": {
                    "configMapKeyRef": {"name": "settings", "key": "OIDC_OP_URL"}
                },
            }
        ]
        cases["configMapKeyRef key"] = config_map_key

        other_version = workload_inventory()
        container_for(other_version["items"][3])["envFrom"] = [
            {"secretRef": {"name": "mastrao-meet-oidc-v7"}}
        ]
        cases["another secret version"] = other_version

        prefixed = workload_inventory()
        container_for(prefixed["items"][5])["envFrom"] = [
            {"prefix": "OIDC_", "configMapRef": {"name": "generic"}}
        ]
        cases["envFrom prefix"] = prefixed

        for case, inventory in cases.items():
            with (
                self.subTest(case=case),
                self.assertRaisesRegex(ValueError, "excluded consumer"),
            ):
                self._build(inventory)

    def test_refuses_oidc_in_another_meet_api_container(self):
        inventory = workload_inventory()
        self._pod_spec_for(inventory["items"][0])["initContainers"] = [
            {
                "name": "bootstrap",
                "image": "example.invalid/x:1",
                "envFrom": [{"secretRef": {"name": SECRET_NAME}}],
            }
        ]
        with self.assertRaisesRegex(ValueError, "already contains unmanaged OIDC"):
            self._build(inventory)

    def test_refuses_to_change_an_existing_managed_non_oidc_variable(self):
        different_value = workload_inventory()
        container_for(different_value["items"][0])["env"] = [
            {"name": "LOGIN_REDIRECT_URL", "value": "https://private.example/secret"}
        ]
        from_secret = workload_inventory()
        container_for(from_secret["items"][0])["env"] = [
            {
                "name": "LOGOUT_REDIRECT_URL",
                "valueFrom": {"secretKeyRef": {"name": "redirects", "key": "out"}},
            }
        ]
        for name, inventory in (
            ("LOGIN_REDIRECT_URL", different_value),
            ("LOGOUT_REDIRECT_URL", from_secret),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as refused:
                    self._build(inventory)
                message = str(refused.exception)
                self.assertIn(f"different value: {name}", message)
                self.assertNotIn("private.example", message)

    def test_accepts_an_identical_existing_managed_variable(self):
        inventory = workload_inventory()
        container_for(inventory["items"][0])["env"] = [
            {"name": "LOGIN_REDIRECT_URL", "value": "https://meet.mastrao-staging.com"},
            {"name": "LOG_LEVEL", "value": "INFO"},
        ]
        plan = self._build(inventory)
        self.assertEqual(
            plan["preservationRequirement"]["observedExistingEnvironmentNames"],
            ["LOGIN_REDIRECT_URL", "LOG_LEVEL"],
        )


if __name__ == "__main__":
    unittest.main()
