"""Tests for room-scoped live transcript publication."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from livekit.agents import LanguageCode, stt

from live_transcriber import GAP_TOPIC, TRANSCRIPTION_TOPIC
from room_audio_live_output import RoomLiveTranscriptionOutput


def _room():
    return SimpleNamespace(
        local_participant=SimpleNamespace(send_text=AsyncMock()),
    )


def _event(*, request_id="room-item-1", text="Bonjour la salle"):
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
        request_id=request_id,
        alternatives=[
            stt.SpeechData(
                language=LanguageCode("fr"),
                text=text,
            )
        ],
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
    """Keep one mixed room stream ordered, anonymous and content-safe."""

    async def test_publishes_unattributed_room_segment_after_sequence_seed(self):
        """A room mix never claims the identity or track of a human speaker."""
        room = _room()
        output = RoomLiveTranscriptionOutput(
            room,
            sequence_seed=1_000,
            leg_id="room-leg-1",
        )

        handle_event = output.create_event_handler()
        await handle_event(_event())

        (payload,) = room.local_participant.send_text.call_args.args
        options = room.local_participant.send_text.call_args.kwargs
        envelope = json.loads(payload)
        self.assertEqual(options["topic"], TRANSCRIPTION_TOPIC)
        self.assertEqual(envelope["participantIdentity"], "unknown-participant")
        self.assertEqual(envelope["trackSid"], "room-mix")
        self.assertEqual(envelope["legId"], "room-leg-1")
        self.assertEqual(envelope["sequence"], 1_001)
        self.assertEqual(envelope["state"], "final")

    async def test_provider_restart_creates_a_distinct_transcript_leg(self):
        """Late events remain bound to the old generation after reconnect."""
        room = _room()
        output = RoomLiveTranscriptionOutput(
            room,
            sequence_seed=1_000,
            leg_id="room-leg-1",
        )
        old_handler = output.create_event_handler()
        new_handler = output.create_event_handler()

        await old_handler(_event(request_id="late-item", text="ancien tardif"))
        await new_handler(_event(request_id="new-item", text="nouveau"))

        payloads = [
            json.loads(call.args[0])
            for call in room.local_participant.send_text.await_args_list
        ]
        self.assertEqual(len(payloads), 1)
        self.assertNotEqual(payloads[0]["legId"], "room-leg-1")
        self.assertEqual(payloads[0]["sequence"], 1_001)

    async def test_failure_publishes_only_a_bounded_gap_reason(self):
        """Provider details and credentials never cross the LiveKit boundary."""
        room = _room()
        output = RoomLiveTranscriptionOutput(room, sequence_seed=2_000)

        await output.handle_failure(RuntimeError("secret provider response"))

        (payload,) = room.local_participant.send_text.call_args.args
        options = room.local_participant.send_text.call_args.kwargs
        envelope = json.loads(payload)
        self.assertEqual(options["topic"], GAP_TOPIC)
        self.assertEqual(envelope["reason"], "provider-error")
        self.assertNotIn("secret", payload)

    async def test_refuses_unbounded_gap_and_post_completion(self):
        """Only known lifecycle gaps and artifact-free live completion pass."""
        output = RoomLiveTranscriptionOutput(_room(), sequence_seed=3_000)
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
