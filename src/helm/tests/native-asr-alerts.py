"""Offline Helm contracts. Every threshold below is a synthetic test value."""

import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

CHART = Path(__file__).resolve().parents[1] / "meet"
POLICY = {
    "service": "fixture-backend",
    "routingOwner": "fixture-oncall",
    "severity": "fixture-severity",
    "prometheusRuleLabels": {"release": "fixture-monitoring"},
    "ready": {"pendingMax": 7, "oldestDueAgeMaxSeconds": 83, "for": "3m"},
    "retry": {"pendingMax": 11, "oldestDueAgeMaxSeconds": 157, "for": "4m"},
}


def render(policy=None, namespace="fixture-staging"):
    values = {}
    if policy is not None:
        values["nativeAsrAlerts"] = policy
    return subprocess.run(
        [
            "helm",
            "template",
            "fixture-meet",
            str(CHART),
            "--namespace",
            namespace,
            "--values",
            "-",
        ],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        check=False,
    )


def documents(manifest):
    return [part.strip() for part in manifest.split("---") if part.strip()]


class NativeAsrAlertTests(unittest.TestCase):
    def test_policy_adds_only_one_rule_and_preserves_every_existing_resource(self):
        baseline = render()
        configured = render(POLICY)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertNotIn("kind: PrometheusRule", baseline.stdout)
        rules = [
            doc for doc in documents(configured.stdout) if "kind: PrometheusRule" in doc
        ]
        self.assertEqual(len(rules), 1)
        existing = [doc for doc in documents(configured.stdout) if doc not in rules]
        self.assertEqual(existing, documents(baseline.stdout))
        self.assertIn("release: fixture-monitoring", rules[0])

    def test_each_lane_has_independent_depth_and_age_rules_with_scoped_gauges(self):
        result = render(POLICY)
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = re.findall(
            r"- alert: (MastraoNativeAsr\w+)\n(.*?)(?=\n        - alert:|\n---|\Z)",
            result.stdout,
            re.DOTALL,
        )
        self.assertEqual(len(blocks), 4)
        for lane in ("ready", "retry"):
            for suffix, metric, key in (
                ("Depth", "pending", "pendingMax"),
                ("Age", "oldest_due_age_seconds", "oldestDueAgeMaxSeconds"),
            ):
                name = f"MastraoNativeAsr{lane.title()}Queue{suffix}"
                block = dict(blocks)[name]
                self.assertIn(
                    f"max(mastrao_native_asr_queue_{metric}"
                    f'{{namespace="fixture-staging",service="fixture-backend",lane="{lane}"}})'
                    f" > {POLICY[lane][key]}",
                    block,
                )
                self.assertIn(f'for: "{POLICY[lane]["for"]}"', block)
                self.assertIn(f'lane: "{lane}"', block)
                self.assertIn('routing_owner: "fixture-oncall"', block)
                self.assertIn('severity: "fixture-severity"', block)
                self.assertNotIn("sum(", block)

    def test_missing_policy_fields_refuse_rendering(self):
        paths = [
            (key,) for key in ("service", "routingOwner", "severity", "ready", "retry")
        ]
        paths += [
            (lane, key)
            for lane in ("ready", "retry")
            for key in ("pendingMax", "oldestDueAgeMaxSeconds", "for")
        ]
        for path in paths:
            with self.subTest(path=path):
                policy = copy.deepcopy(POLICY)
                parent = policy
                for key in path[:-1]:
                    parent = parent[key]
                del parent[path[-1]]
                result = render(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts", result.stderr)

    def test_invalid_thresholds_and_durations_refuse_rendering(self):
        for key, value in (
            ("pendingMax", -1),
            ("pendingMax", 1.5),
            ("pendingMax", True),
            ("oldestDueAgeMaxSeconds", "1 or vector(1)"),
            ("for", ""),
            ("for", "tomorrow"),
            ("for", "-1m"),
        ):
            with self.subTest(key=key, value=value):
                policy = copy.deepcopy(POLICY)
                policy["ready"][key] = value
                result = render(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts.ready", result.stderr)

    def test_explicit_zero_threshold_and_immediate_hold_are_supported(self):
        policy = copy.deepcopy(POLICY)
        policy["ready"] = {"pendingMax": 0, "oldestDueAgeMaxSeconds": 0, "for": "0s"}
        result = render(policy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lane="ready"}) > 0', result.stdout)
        self.assertIn('for: "0s"', result.stdout)

    def test_selector_and_routing_fields_reject_non_string_values(self):
        for field in ("service", "routingOwner", "severity"):
            for value in (123, True, False, None, [], {"name": "fixture"}):
                with self.subTest(field=field, value=value):
                    policy = copy.deepcopy(POLICY)
                    policy[field] = value
                    result = render(policy)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"nativeAsrAlerts.{field}", result.stderr)

    def test_service_rejects_invalid_kubernetes_service_names(self):
        for value in (
            "",
            " ",
            "123",
            "UPPER",
            "-backend",
            "backend-",
            "foo.bar",
            'fixture"backend',
            "fixture\\backend",
            "backend\nname",
            "a" * 64,
        ):
            with self.subTest(value=value):
                policy = copy.deepcopy(POLICY)
                policy["service"] = value
                result = render(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts.service", result.stderr)

    def test_prometheus_rule_labels_reject_non_string_values(self):
        for value in (123, True, False, None, [], {}):
            with self.subTest(value=value):
                policy = copy.deepcopy(POLICY)
                policy["prometheusRuleLabels"]["release"] = value
                result = render(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts.prometheusRuleLabels", result.stderr)

    def test_policy_and_nested_maps_reject_wrong_types(self):
        for value in (123, True, False, "fixture", [], ["fixture"]):
            with self.subTest(policy=value):
                result = render(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts", result.stderr)
            for field in ("ready", "retry", "prometheusRuleLabels"):
                with self.subTest(field=field, value=value):
                    policy = copy.deepcopy(POLICY)
                    policy[field] = value
                    result = render(policy)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"nativeAsrAlerts.{field}", result.stderr)

    def test_routing_strings_and_kubernetes_label_values_reject_invalid_format(self):
        for field in ("routingOwner", "severity"):
            for value in ("", " ", " owner", "owner ", "owner\nname", "owner\x00"):
                with self.subTest(field=field, value=value):
                    policy = copy.deepcopy(POLICY)
                    policy[field] = value
                    result = render(policy)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"nativeAsrAlerts.{field}", result.stderr)
        for value in (" ", "-label", "label-", "label/name", "a" * 64):
            with self.subTest(label=value):
                policy = copy.deepcopy(POLICY)
                policy["prometheusRuleLabels"]["release"] = value
                result = render(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nativeAsrAlerts.prometheusRuleLabels", result.stderr)

    def test_string_values_are_not_coerced_to_yaml_booleans_or_numbers(self):
        policy = copy.deepcopy(POLICY)
        policy["service"] = "true"
        policy["routingOwner"] = "123"
        policy["severity"] = "true"
        policy["prometheusRuleLabels"] = {"release": "123", "enabled": "true"}
        result = render(policy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('service="true"', result.stdout)
        self.assertIn('routing_owner: "123"', result.stdout)
        self.assertIn('severity: "true"', result.stdout)
        self.assertIn('release: "123"', result.stdout)
        self.assertIn('enabled: "true"', result.stdout)

    def test_namespace_and_service_are_exact_matchers(self):
        policy = copy.deepcopy(POLICY)
        policy["service"] = "a" * 63
        result = render(policy, namespace="another-staging")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'namespace="another-staging",service=' + json.dumps(policy["service"]),
            result.stdout,
        )
        self.assertNotIn("namespace=~", result.stdout)
        self.assertNotIn("service=~", result.stdout)


if __name__ == "__main__":
    unittest.main()
