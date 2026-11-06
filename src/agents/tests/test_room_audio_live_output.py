"""Tests for track-owned live transcript publication."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from live_transcript_event import LiveTranscriptEvent, LiveTranscriptEventKind
from live_transcription_publisher import GAP_TOPIC, TRANSCRIPTION_TOPIC
from room_audio_live_output import RoomLiveTranscriptionOutput
from room_audio_runtime import (
    ProviderSessionCapacityUnavailable,
    TrackSourceBinding,
)


def _room():
    return SimpleNamespace(local_participant=SimpleNamespace(send_text=AsyncMock()))


def _event(text="Bonjour la salle"):
    return LiveTranscriptEvent(
        kind=LiveTranscriptEventKind.COMPLETE,
        logical_unit_id="room-item-1",
        local_sequence=1,
        text=text,
        language="fr",
    )


def _binding(identity="matthias", track_ref="TR_matthias"):
    return TrackSourceBinding(
        participant_identity=identity,
        track_ref=track_ref,
        authority_generation=1,
        session_generation=1,
    )


def _mixer_snapshot():
    return SimpleNamespace(
        authorized_track_count=3,
        pending_track_count=0,
        excluded_track_count=0,
        output_frame_count=500,
        audio_bytes=480_000,
        dropped_frame_count=0,
        track_underrun_frame_count=4,
    )


class RoomLiveTranscriptionOutputTests(unittest.IsolatedAsyncioTestCase):
    """Publish only exact source identities and content-safe failures."""

    async def test_publishes_under_the_bound_track_owner(self):
        """The publisher uses the identity and track fixed before ASR."""
        room = _room()
        output = RoomLiveTranscriptionOutput(
            room,
            sequence_seed=1_000,
            leg_id="track-leg-1",
        )

        handler = output.create_track_event_handler(_binding())
        await handler(_event())

        envelope = json.loads(room.local_participant.send_text.call_args.args[0])
        options = room.local_participant.send_text.call_args.kwargs
        self.assertEqual(options["topic"], TRANSCRIPTION_TOPIC)
        self.assertEqual(envelope["participantIdentity"], "matthias")
        self.assertEqual(envelope["trackSid"], "TR_matthias")
        self.assertEqual(envelope["legId"], "track-leg-1")
        self.assertEqual(envelope["sequence"], 1_001)

    async def test_preserves_zero_as_a_canonical_provider_timestamp(self):
        """Epoch-relative zero is data, not a missing timestamp."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=1_100)
        event = LiveTranscriptEvent(
            kind=LiveTranscriptEventKind.COMPLETE,
            logical_unit_id="room-item-zero",
            local_sequence=1,
            text="Début exact",
            start_time_ms=0,
            end_time_ms=1,
        )

        await output.create_track_event_handler(_binding())(event)

        envelope = json.loads(room.local_participant.send_text.call_args.args[0])
        self.assertEqual(envelope["startTime"], 0)
        self.assertEqual(envelope["endTime"], 1)

    async def test_publishes_overlapping_tracks_under_distinct_owners(self):
        """Concurrent transcript legs retain distinct microphone owners."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=2_000)

        await output.create_track_event_handler(_binding("matthias", "TR_a"))(
            _event("A")
        )
        await output.create_track_event_handler(_binding("lucas", "TR_b"))(_event("B"))

        envelopes = [
            json.loads(call.args[0])
            for call in room.local_participant.send_text.await_args_list
        ]
        self.assertEqual(
            [(item["participantIdentity"], item["trackSid"]) for item in envelopes],
            [("matthias", "TR_a"), ("lucas", "TR_b")],
        )

    async def test_duplicate_and_out_of_order_provider_events_are_ignored(self):
        """Retries and delayed events cannot duplicate terminal transcript text."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=2_500)
        handler = output.create_track_event_handler(_binding())
        complete = _event("Texte final")
        delayed = LiveTranscriptEvent(
            kind=LiveTranscriptEventKind.APPEND,
            logical_unit_id="room-item-1",
            local_sequence=1,
            text="Texte ancien",
            language="fr",
        )

        await handler(complete)
        await handler(complete)
        await handler(delayed)

        self.assertEqual(room.local_participant.send_text.await_count, 1)

    async def test_append_snapshot_and_complete_have_distinct_semantics(self):
        """Canonical event kinds preserve text without provider assumptions."""
        room = _room()
        handler = RoomLiveTranscriptionOutput(
            room, sequence_seed=2_600
        ).create_track_event_handler(_binding())
        events = [
            LiveTranscriptEvent(
                kind=LiveTranscriptEventKind.APPEND,
                logical_unit_id="unit-1",
                local_sequence=1,
                text="Bonjour ",
            ),
            LiveTranscriptEvent(
                kind=LiveTranscriptEventKind.APPEND,
                logical_unit_id="unit-1",
                local_sequence=2,
                text="Matthias",
            ),
            LiveTranscriptEvent(
                kind=LiveTranscriptEventKind.SNAPSHOT,
                logical_unit_id="unit-1",
                local_sequence=3,
                text="Salut Matthias",
            ),
            LiveTranscriptEvent(
                kind=LiveTranscriptEventKind.COMPLETE,
                logical_unit_id="unit-1",
                local_sequence=4,
                text="Salut Matthias.",
            ),
        ]

        for event in events:
            await handler(event)

        envelopes = [
            json.loads(call.args[0])
            for call in room.local_participant.send_text.await_args_list
        ]
        self.assertEqual(
            [(item["text"], item["state"]) for item in envelopes],
            [
                ("Bonjour", "interim"),
                ("Bonjour Matthias", "interim"),
                ("Salut Matthias", "interim"),
                ("Salut Matthias.", "final"),
            ],
        )
        for envelope in envelopes:
            self.assertNotIn("language", envelope)
            self.assertNotIn(None, envelope.values())

    async def test_transport_failure_does_not_consume_local_sequence(self):
        """The exact event can retry after an unconfirmed LiveKit send."""
        room = _room()
        room.local_participant.send_text.side_effect = [
            RuntimeError("transport failed"),
            None,
        ]
        handler = RoomLiveTranscriptionOutput(
            room, sequence_seed=2_700
        ).create_track_event_handler(_binding())
        event = _event("Texte à retenter")

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            await handler(event)
        await handler(event)

        self.assertEqual(room.local_participant.send_text.await_count, 2)
        retry_envelope = json.loads(
            room.local_participant.send_text.await_args_list[1].args[0]
        )
        self.assertEqual(retry_envelope["text"], "Texte à retenter")

    async def test_failure_publishes_only_a_bounded_gap_reason(self):
        """Provider errors expose no content or credential details."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=3_000)

        await output.handle_failure(RuntimeError("secret provider response"))

        payload = room.local_participant.send_text.call_args.args[0]
        envelope = json.loads(payload)
        self.assertEqual(
            room.local_participant.send_text.call_args.kwargs["topic"], GAP_TOPIC
        )
        self.assertEqual(envelope["reason"], "provider-error")
        self.assertNotIn("secret", payload)

    async def test_capacity_failure_publishes_recoverable_gap_reason(self):
        """Capacity pressure is distinguishable from provider failure."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=3_100)

        await output.handle_failure(ProviderSessionCapacityUnavailable("room"))

        payload = room.local_participant.send_text.call_args.args[0]
        envelope = json.loads(payload)
        self.assertEqual(envelope["reason"], "provider-capacity")

    async def test_refuses_unbounded_gap_and_post_completion(self):
        """The live output rejects undeclared reasons and post artifacts."""
        output = RoomLiveTranscriptionOutput(_room(), sequence_seed=4_000)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            await output.publish_gap("provider-secret-detail")
        with self.assertRaisesRegex(ValueError, "live pipeline"):
            await output.complete(
                SimpleNamespace(effect={"pipeline": "post"}, artifact=None)
            )
        with self.assertRaisesRegex(ValueError, "cannot contain"):
            await output.complete(
                SimpleNamespace(effect={"pipeline": "live"}, artifact=object())
            )

        await output.complete(
            SimpleNamespace(
                effect={"pipeline": "live"},
                artifact=None,
                mixer=_mixer_snapshot(),
            )
        )


if __name__ == "__main__":
    unittest.main()
