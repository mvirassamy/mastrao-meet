"""Native allowlist and canonical v3 bytes, with no provider or storage access."""

import hashlib
import unittest

from live_delivery_codec import event_bytes, validate_receipt
from live_model_evidence import MODEL_WHITESPACE, ModelEvidenceCollector
from live_send_control import ControlError


def model_record(**changes):
    """Build one explicit record without inferring any requested model facts."""
    return {
        "origin": "terminal",
        "model_ref": "model-😀",
        "name": ' e\u0301 é "\\\n ',
        "version": None,
        "architecture": None,
        **changes,
    }


def model_event(**changes):
    """Share an exact Unicode fixture with the independent Core byte check."""
    return {
        "version": 3,
        "source_ref": "source_reference_0001",
        "leg_ref": "initial_leg_reference_0001",
        "sequence": 1,
        "kind": "model_evidence",
        "provider": "deepgram",
        "models": [model_record()],
        "truncated": False,
        **changes,
    }


class ModelEvidenceTests(unittest.TestCase):
    """Treat missing evidence, omitted invalid facts and received facts distinctly."""

    def test_empty_providers_do_not_infer_requested_options(self):
        """A selected profile never fills in missing provider evidence."""
        for provider in ("openai", "mistral", "deepgram"):
            with self.subTest(provider=provider):
                collector = ModelEvidenceCollector(provider)
                collector.observe({"type": "error", "model": "untrusted-error-text"})
                self.assertEqual(
                    collector.snapshot,
                    {"provider": provider, "models": [], "truncated": False},
                )

    def test_openai_uses_only_received_asr_model_not_conversation_or_usage(self):
        """Only native ASR session models survive the allowlist."""
        collector = ModelEvidenceCollector("openai")
        event = {
            "type": "session.updated",
            "session": {
                "model": "conversation-model",
                "audio": {
                    "input": {
                        "transcription": {
                            "model": "received-asr-build",
                            "prompt": "PRIVATE PROMPT",
                        }
                    }
                },
                "client_secret": {"value": "PRIVATE TOKEN"},
            },
        }
        collector.observe(event)
        collector.observe(event)
        collector.observe(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "model": "not-in-this-schema",
                "transcript": "PRIVATE TEXT",
            }
        )
        self.assertEqual(
            collector.snapshot["models"],
            [model_record(origin="session", model_ref="received-asr-build", name=None)],
        )
        self.assertFalse(collector.snapshot["truncated"])
        copy = collector.snapshot
        copy["models"][0]["model_ref"] = "changed"
        self.assertEqual(
            collector.snapshot["models"][0]["model_ref"], "received-asr-build"
        )

    def test_deepgram_terminal_mapping_and_results_remain_distinct_in_received_order(
        self,
    ):
        """Terminal models may be absent, IDs alone, or mapped build details."""
        collector = ModelEvidenceCollector("deepgram")
        collector.observe(
            {
                "type": "Results",
                "metadata": {
                    "model_uuid": "build-a",
                    "model_info": {
                        "name": "native-name",
                        "version": "native-version",
                        "arch": "native-arch",
                        "text": "PRIVATE TEXT",
                    },
                },
            }
        )
        collector.observe(
            {
                "type": "Metadata",
                "models": ["build-b", "build-a"],
                "model_info": {"build-a": {"name": "terminal-name"}},
                "transaction_key": "PRIVATE TOKEN",
            }
        )
        self.assertEqual(
            collector.snapshot["models"],
            [
                model_record(
                    origin="result",
                    model_ref="build-a",
                    name="native-name",
                    version="native-version",
                    architecture="native-arch",
                ),
                model_record(model_ref="build-b", name=None),
                model_record(model_ref="build-a", name="terminal-name"),
            ],
        )
        self.assertFalse(collector.snapshot["truncated"])

    def test_invalid_fields_and_overflow_are_bounded_without_throwing(self):
        """UTF-16 overflow, malformed maps and excess facts latch truncation only."""
        for value in ("", " ", 1, True, [], {}, "x" * 161, "😀" * 81, "\ud800"):
            with self.subTest(value=repr(value)):
                collector = ModelEvidenceCollector("mistral")
                collector.observe({"type": "transcription.done", "model": value})
                self.assertEqual(collector.snapshot["models"], [])
                self.assertTrue(collector.snapshot["truncated"])
        collector = ModelEvidenceCollector("deepgram")
        collector.observe(
            {"type": "Metadata", "models": [[], "😀" * 80], "model_info": False}
        )
        self.assertEqual(
            collector.snapshot["models"], [model_record(model_ref="😀" * 80, name=None)]
        )
        for number in range(12):
            collector.observe(
                {"type": "Results", "metadata": {"model_uuid": str(number)}}
            )
        self.assertEqual(len(collector.snapshot["models"]), 8)
        self.assertTrue(collector.snapshot["truncated"])

    def test_nonblank_matches_core_javascript_without_trimming_received_values(self):
        """Reject BOM/Unicode whitespace but preserve strings JS considers nonblank."""
        for value in (*MODEL_WHITESPACE, MODEL_WHITESPACE):
            collector = ModelEvidenceCollector("mistral")
            collector.observe({"type": "transcription.done", "model": value})
            self.assertEqual(collector.snapshot["models"], [])
            self.assertTrue(collector.snapshot["truncated"])
        for value in ("\x85", "\x1c", "\ufeffmodel\ufeff"):
            collector = ModelEvidenceCollector("mistral")
            collector.observe({"type": "transcription.done", "model": value})
            self.assertEqual(collector.snapshot["models"][0]["model_ref"], value)
            self.assertFalse(collector.snapshot["truncated"])
            event_bytes(model_event(**collector.snapshot))


class ModelEvidenceCodecTests(unittest.TestCase):
    """Validate the frozen wire contract while retaining v2 receipt semantics."""

    def test_exact_unicode_canonical_bytes_and_v2_receipt(self):
        """Canonical order preserves exact Unicode against independent bytes."""
        event = model_event()
        expected = (
            '{"version":3,"source_ref":"source_reference_0001",'
            '"leg_ref":"initial_leg_reference_0001","sequence":1,'
            '"kind":"model_evidence","provider":"deepgram","models":['
            '{"origin":"terminal","model_ref":"model-😀","name":" e\u0301 é '
            '\\"\\\\\\n ","version":null,"architecture":null}],"truncated":false}'
        ).encode()
        event["models"][0] = dict(reversed(list(event["models"][0].items())))
        self.assertEqual(event_bytes(dict(reversed(list(event.items())))), expected)
        validate_receipt(
            {
                "version": 2,
                "source_ref": event["source_ref"],
                "leg_ref": event["leg_ref"],
                "sequence": 1,
                "event_digest": hashlib.sha256(expected).hexdigest(),
                "state": "collecting",
                "confirmed_sequence": 1,
                "final_event_count": 0,
                "incomplete": False,
                "meeting_coverage": "unverified",
            },
            event,
            0,
        )

    def test_wire_refuses_unbounded_or_ambiguous_metadata(self):
        """Reject unknown fields, invalid records and overflow at the wire boundary."""
        for changes in (
            {"version": 2},
            {"version": True},
            {"provider": "other"},
            {"provider": []},
            {"models": {}},
            {"models": [model_record()] * 9},
            {"models": [model_record()] * 2},
            {"truncated": 1},
            {"raw": {}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ControlError):
                event_bytes(model_event(**changes))
        for record in (
            model_record(origin="handshake"),
            model_record(origin=[]),
            model_record(model_ref=None, name=None),
            model_record(extra="private"),
            model_record(version=" "),
            model_record(name="😀" * 81),
        ):
            with self.subTest(record=record), self.assertRaises(ControlError):
                event_bytes(model_event(models=[record]))
        event_bytes(model_event(models=[]))
        event_bytes(model_event(models=[model_record(name="😀" * 80)]))
