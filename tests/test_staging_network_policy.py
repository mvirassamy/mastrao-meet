import json
import unittest
from ipaddress import ip_network
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "deploy/kubernetes/staging/meet-api-network-policy.json"
AGENT_POLICY_PATH = (
    ROOT / "deploy/kubernetes/staging/agent-subtitles-network-policy.json"
)
COLLECTOR_POLICY_PATH = (
    ROOT / "deploy/kubernetes/staging/metadata-collector-network-policy.json"
)
NATIVE_ASR_WORKER_POLICY_PATH = (
    ROOT / "deploy/kubernetes/staging/native-asr-worker-network-policy.json"
)
NATIVE_ASR_DESTINATION_POLICY_PATH = (
    ROOT
    / "deploy/kubernetes/staging/native-asr-worker-destination-network-policy.json"
)
PLATFORM_HOST = "app.mastrao-staging.com"


class StagingNetworkPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(POLICY_PATH.read_text())
        cls.spec = cls.policy["spec"]

    def test_targets_only_the_staging_meet_api(self):
        self.assertEqual(self.policy["apiVersion"], "cilium.io/v2")
        self.assertEqual(self.policy["kind"], "CiliumNetworkPolicy")
        self.assertEqual(
            self.policy["metadata"],
            {"name": "meet-api-private-start", "namespace": "mastrao-staging"},
        )
        self.assertEqual(
            self.spec["endpointSelector"]["matchLabels"],
            {
                "app.kubernetes.io/name": "meet-api",
                "mastrao.com/component": "meet-api",
            },
        )

    def test_allows_platform_dns_resolution(self):
        dns_names = []
        for rule in self.spec["egress"]:
            for port_rule in rule.get("toPorts", []):
                ports = port_rule.get("ports", [])
                if {"port": "53", "protocol": "ANY"} not in ports:
                    continue
                dns_names.extend(
                    entry.get("matchName")
                    for entry in port_rule.get("rules", {}).get("dns", [])
                )

        self.assertEqual(dns_names.count(PLATFORM_HOST), 1)

    def test_allows_only_https_for_platform_egress(self):
        platform_rules = [
            rule
            for rule in self.spec["egress"]
            if {"matchName": PLATFORM_HOST} in rule.get("toFQDNs", [])
        ]

        self.assertEqual(len(platform_rules), 1)
        self.assertEqual(
            platform_rules[0]["toPorts"],
            [{"ports": [{"port": "443", "protocol": "TCP"}]}],
        )

    def test_allows_only_room_audio_worker_to_publish_on_meet_api(self):
        room_audio_rules = [
            rule
            for rule in self.spec["ingress"]
            if any(
                endpoint.get("matchLabels", {}).get(
                    "k8s:app.kubernetes.io/component"
                )
                == "agent-room-audio"
                for endpoint in rule.get("fromEndpoints", [])
            )
        ]

        self.assertEqual(len(room_audio_rules), 1)
        self.assertEqual(
            room_audio_rules[0]["fromEndpoints"],
            [
                {
                    "matchLabels": {
                        "k8s:app.kubernetes.io/component": "agent-room-audio",
                        "k8s:app.kubernetes.io/name": "room-audio-worker",
                        "k8s:io.kubernetes.pod.namespace": "mastrao-staging",
                    }
                }
            ],
        )
        self.assertEqual(
            room_audio_rules[0]["toPorts"],
            [{"ports": [{"port": "8000", "protocol": "TCP"}]}],
        )

    def test_routes_core_egress_through_the_bounded_relay(self):
        core_rules = [
            rule
            for rule in self.spec["egress"]
            if any(
                endpoint.get("matchLabels", {}).get(
                    "k8s:io.cilium.k8s.policy.serviceaccount"
                )
                == "cabinet-core"
                for endpoint in rule.get("toEndpoints", [])
            )
        ]

        self.assertEqual(len(core_rules), 1)
        self.assertEqual(
            core_rules[0]["toPorts"],
            [{"ports": [{"port": "18081", "protocol": "TCP"}]}],
        )

    def test_has_no_wildcard_or_unexpected_cidr_egress(self):
        serialized = json.dumps(self.spec)
        self.assertNotIn("matchPattern", serialized)

        cidrs = [
            ip_network(cidr)
            for rule in self.spec["egress"]
            for cidr in rule.get("toCIDR", [])
        ]
        self.assertEqual(cidrs, [ip_network("172.16.8.8/32")])
        self.assertTrue(all(network.is_private for network in cidrs))
        self.assertNotIn("toCIDRSet", serialized)

    def test_has_no_open_entity_or_unexpected_fqdn_egress(self):
        entities = {
            entity
            for rule in self.spec["egress"]
            for entity in rule.get("toEntities", [])
        }
        self.assertEqual(entities, {"remote-node", "host"})

        fqdns = sorted(
            entry["matchName"]
            for rule in self.spec["egress"]
            for entry in rule.get("toFQDNs", [])
        )
        self.assertEqual(
            fqdns,
            sorted([
                "s3.fr-par.scw.cloud",
                "mastrao-staging-meet-egress-captures.s3.fr-par.scw.cloud",
                PLATFORM_HOST,
            ]),
        )


class StagingWorkerNetworkPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = json.loads(AGENT_POLICY_PATH.read_text())
        cls.collector = json.loads(COLLECTOR_POLICY_PATH.read_text())

    @staticmethod
    def _dns_names(policy):
        return {
            entry["matchName"]
            for rule in policy["spec"]["egress"]
            for port_rule in rule.get("toPorts", [])
            if {"port": "53", "protocol": "ANY"}
            in port_rule.get("ports", [])
            for entry in port_rule.get("rules", {}).get("dns", [])
        }

    @staticmethod
    def _fqdn_names(policy):
        return {
            entry["matchName"]
            for rule in policy["spec"]["egress"]
            for entry in rule.get("toFQDNs", [])
        }

    def test_policies_select_workers_by_component(self):
        self.assertEqual(
            self.agent["spec"]["endpointSelector"]["matchLabels"],
            {
                "app.kubernetes.io/name": "meet",
                "app.kubernetes.io/component": "agent-subtitles",
            },
        )
        self.assertEqual(
            self.collector["spec"]["endpointSelector"]["matchLabels"],
            {
                "app.kubernetes.io/name": "meet",
                "app.kubernetes.io/component": "agent-metadata",
            },
        )

    def test_openai_egress_is_limited_to_subtitle_agent(self):
        self.assertIn("api.openai.com", self._dns_names(self.agent))
        self.assertEqual(self._fqdn_names(self.agent), {"api.openai.com"})
        self.assertNotIn("api.openai.com", self._dns_names(self.collector))
        self.assertNotIn("api.openai.com", self._fqdn_names(self.collector))

    def test_collector_preserves_core_and_s3_egress(self):
        self.assertEqual(
            self._dns_names(self.collector),
            {
                "livekit-signal.mastrao-staging.svc.cluster.local",
                "cabinet-core.mastrao-staging.svc.cluster.local",
                "s3.fr-par.scw.cloud",
                "mastrao-staging-meet-egress-captures.s3.fr-par.scw.cloud",
            },
        )
        self.assertEqual(
            self._fqdn_names(self.collector),
            {
                "s3.fr-par.scw.cloud",
                "mastrao-staging-meet-egress-captures.s3.fr-par.scw.cloud",
            },
        )
        core_rules = [
            rule
            for rule in self.collector["spec"]["egress"]
            if any(
                endpoint.get("matchLabels", {}).get(
                    "k8s:io.cilium.k8s.policy.serviceaccount"
                )
                == "cabinet-core"
                for endpoint in rule.get("toEndpoints", [])
            )
        ]
        self.assertEqual(len(core_rules), 1)
        self.assertEqual(
            core_rules[0]["toPorts"],
            [{"ports": [{"port": "18081", "protocol": "TCP"}]}],
        )


class NativeAsrWorkerNetworkPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = json.loads(NATIVE_ASR_WORKER_POLICY_PATH.read_text())
        cls.destinations = json.loads(
            NATIVE_ASR_DESTINATION_POLICY_PATH.read_text()
        )

    def test_selects_only_the_ready_and_retry_workers(self):
        self.assertEqual(
            self.worker["spec"]["endpointSelector"]["matchExpressions"],
            [
                {
                    "key": "app.kubernetes.io/name",
                    "operator": "In",
                    "values": [
                        "worker-native-asr-ready",
                        "worker-native-asr-retry",
                    ],
                }
            ],
        )
        self.assertEqual(self.worker["spec"]["ingress"], [])

    def test_worker_egress_is_limited_to_required_dependencies(self):
        spec = self.worker["spec"]
        serialized = json.dumps(spec)
        self.assertNotIn("matchPattern", serialized)
        self.assertEqual(
            [cidr for rule in spec["egress"] for cidr in rule.get("toCIDR", [])],
            ["172.16.8.8/32"],
        )
        self.assertEqual(
            {
                endpoint["matchLabels"].get("k8s:app.kubernetes.io/name")
                for rule in spec["egress"]
                for endpoint in rule.get("toEndpoints", [])
                if endpoint["matchLabels"].get("k8s:app.kubernetes.io/name")
            },
            {"cabinet-core", "redis-meet"},
        )

        entity_rules = [
            rule for rule in spec["egress"] if "toEntities" in rule
        ]
        self.assertEqual(len(entity_rules), 1)
        self.assertEqual(entity_rules[0]["toEntities"], ["remote-node", "host"])
        self.assertEqual(
            entity_rules[0]["toPorts"],
            [{"ports": [{"port": "8787", "protocol": "TCP"}]}],
        )

    def test_destinations_allow_only_worker_lanes_on_bounded_ports(self):
        self.assertEqual(len(self.destinations["specs"]), 1)
        allowed = {}
        for spec in self.destinations["specs"]:
            destination = spec["endpointSelector"]["matchLabels"][
                "app.kubernetes.io/name"
            ]
            ingress = spec["ingress"]
            self.assertEqual(len(ingress), 1)
            source = ingress[0]["fromEndpoints"][0]
            self.assertEqual(
                source["matchExpressions"][0]["values"],
                ["worker-native-asr-ready", "worker-native-asr-retry"],
            )
            self.assertEqual(
                source["matchLabels"],
                {"k8s:io.kubernetes.pod.namespace": "mastrao-staging"},
            )
            allowed[destination] = ingress[0]["toPorts"][0]["ports"]

        self.assertEqual(
            allowed,
            {
                "cabinet-core": [{"port": "18081", "protocol": "TCP"}],
            },
        )


if __name__ == "__main__":
    unittest.main()
