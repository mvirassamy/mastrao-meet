"""Preserve bounded metadata and deliver explicit persistent speaker receipts."""

import base64
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from exceptions import MissingConfigError
from metadata_collector import MetadataCollector


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _metadata(persistent):
    value = {
        "version": 1,
        "recording_id": "recording_persistent_test",
        "recording_ref": "recording_persistent_test",
        "meeting_ref": "meeting_persistent_test",
        "room_ref": "room_persistent_test",
        "evidence_ref": "evidence_0123456789abcdef0123456789abcdef",
        "organization_external_id": "organization_persistent_test",
        "provider_binding_digest": "a" * 64,
        "policy_ref": "policy_persistent_test",
        "notice_version": "notice_persistent_test",
        "notice_digest": "b" * 64,
        "retention_expires_at": 4100000000,
        "recording_started_at_ms": 1000,
        "object_ref": "mastrao-speaker-evidence/evidence_0123456789abcdef0123456789abcdef.json",
    }
    if persistent:
        value.update(version=2, retention_mode="until_deleted", retention_expires_at=None)
    return value


class MetadataRetentionTests(unittest.TestCase):
    """Run the real collector/signature path with storage and HTTP peers mocked."""

    def setUp(self):
        """Configure synthetic keys and replace all external peer boundaries."""
        self.key = Ed25519PrivateKey.generate()
        environ = {
            "AWS_STORAGE_BUCKET_NAME": "test-only-bucket",
            "MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK": json.dumps({
                "kty": "OKP", "crv": "Ed25519",
                "d": _b64(self.key.private_bytes_raw()),
                "x": _b64(self.key.public_key().public_bytes_raw()),
            }),
            "MASTRAO_RECORDING_RECEIPT_KEY_ID": "collector-test-key",
            "MASTRAO_RECORDING_RECEIPT_ISSUER": "meet-collector-test",
            "MASTRAO_RECORDING_RECEIPT_AUDIENCE": "core-collector-test",
            "MASTRAO_RECORDING_REGION_REF": "test-region",
            "MASTRAO_RECORDING_ENCRYPTION_REF": "test-encryption",
            "MASTRAO_RECORDING_LIFECYCLE_POLICY_REF": "test-lifecycle",
            "MASTRAO_CORE_SPEAKER_EVIDENCE_ARTIFACT_ENDPOINT":
                "http://localhost/internal/v1/meetings/speaker-evidence/artifacts/finalize",
        }
        environment = patch.dict(os.environ, environ)
        environment.start()
        self.addCleanup(environment.stop)
        storage = patch("metadata_collector.Minio")
        self.storage = storage.start().return_value
        self.addCleanup(storage.stop)
        response = Mock(status=200)
        response.read.return_value = b'{"state":"available","outcome":"available"}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        http = patch("metadata_collector.request.build_opener", return_value=opener)
        http.start()
        self.addCleanup(http.stop)
        self.opened = opener.open

    def _save_receipt(self, metadata):
        collector = MetadataCollector(SimpleNamespace(), json.dumps(metadata))
        collector.save()
        request = self.opened.call_args.args[0]
        compact = json.loads(request.data)["speaker_evidence_artifact_receipt"]
        header, encoded, signature = compact.split(".")
        decoded_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        self.key.public_key().verify(decoded_signature, f"{header}.{encoded}".encode())
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(claims["expires_at"] - claims["issued_at"], 30)
        self.assertEqual(claims["organization_external_id"], metadata["organization_external_id"])
        self.assertEqual(claims["object_ref"], metadata["object_ref"])
        self.assertEqual(self.storage.put_object.call_count, 2)
        self.storage.remove_object.assert_called_once()
        return claims

    def test_bounded_metadata_keeps_old_receipt_shape(self):
        """Old finite metadata never gains a new signed mode field."""
        claims = self._save_receipt(_metadata(False))
        self.assertEqual(claims["operation_version"], 1)
        self.assertEqual(claims["retention_expires_at"], 4100000000)
        self.assertNotIn("retention_mode", claims)

    def test_persistent_metadata_emits_version2_receipt(self):
        """The real save/sign/callback path preserves explicit persistence."""
        claims = self._save_receipt(_metadata(True))
        self.assertEqual(claims["operation_version"], 2)
        self.assertEqual(claims["retention_mode"], "until_deleted")
        self.assertIsNone(claims["retention_expires_at"])

    def test_unknown_historical_null_cannot_be_signed_as_persistent(self):
        """Missing historical retention fails closed before a signed callback."""
        metadata = _metadata(False)
        metadata["retention_expires_at"] = None
        collector = MetadataCollector(SimpleNamespace(), json.dumps(metadata))
        with self.assertRaises(MissingConfigError):
            collector.save()
        self.opened.assert_not_called()

    def test_persistent_metadata_requires_exact_version_mode_and_null(self):
        """Neither a coerced boolean nor an old version can imply permanence."""
        for change in (
            {"version": 1}, {"version": True},
            {"retention_mode": "bounded"}, {"retention_mode": "unknown"},
            {"retention_expires_at": False}, {"retention_expires_at": 4100000000},
        ):
            with self.subTest(change=change), self.assertRaises(MissingConfigError):
                MetadataCollector(SimpleNamespace(), json.dumps({**_metadata(True), **change}))
        metadata = _metadata(True)
        del metadata["retention_mode"]
        with self.assertRaises(MissingConfigError):
            MetadataCollector(SimpleNamespace(), json.dumps(metadata))
        self.opened.assert_not_called()
