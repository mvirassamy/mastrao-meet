import json
import unittest
from ipaddress import ip_network
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "deploy/kubernetes/staging/meet-api-network-policy.json"
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
        # "world" or "all" entities would open the Internet without any CIDR.
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


if __name__ == "__main__":
    unittest.main()
