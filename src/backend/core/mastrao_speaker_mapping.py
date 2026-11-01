"""Map diarized Room Audio segments to consented participant labels."""

ARTIFACT_SCHEMA_VERSION = 1


def _participant_labels(evidence):
    labels = {}
    for participant in evidence.get("participants", []):
        if not isinstance(participant, dict):
            continue
        participant_ref = participant.get("participant_ref")
        events = participant.get("display_name_events", [])
        if not isinstance(participant_ref, str) or not isinstance(events, list):
            continue
        candidates = [
            event
            for event in events
            if isinstance(event, dict)
            and isinstance(event.get("label"), str)
            and event["label"].strip()
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda event: (
                event["effective_at_ms"]
                if isinstance(event.get("effective_at_ms"), int)
                and not isinstance(event.get("effective_at_ms"), bool)
                else -1
            )
        )
        label = candidates[-1]["label"].strip()
        if label.lower() != "anonymous":
            labels[participant_ref] = label
    return labels


def _speech_intervals(evidence):
    open_since = {}
    intervals = {}
    events = evidence.get("events", [])
    if not isinstance(events, list):
        return intervals
    ordered = sorted(
        (event for event in events if isinstance(event, dict)),
        key=lambda event: (
            event["at_ms"]
            if isinstance(event.get("at_ms"), int)
            and not isinstance(event.get("at_ms"), bool)
            else -1
        ),
    )
    for event in ordered:
        participant_ref = event.get("participant_ref")
        at_ms = event.get("at_ms")
        if not isinstance(participant_ref, str) or not isinstance(at_ms, int):
            continue
        if event.get("type") == "speech_start":
            open_since[participant_ref] = at_ms
        if event.get("type") == "speech_end":
            start_ms = open_since.pop(participant_ref, None)
            if isinstance(start_ms, int) and at_ms > start_ms:
                intervals.setdefault(participant_ref, []).append((start_ms, at_ms))
    timeline_end = evidence.get("timeline_ended_at_ms")
    if isinstance(timeline_end, int):
        for participant_ref, start_ms in open_since.items():
            if timeline_end > start_ms:
                intervals.setdefault(participant_ref, []).append(
                    (start_ms, timeline_end)
                )
    return intervals


def _overlap_ms(segment, interval):
    return max(
        0,
        min(segment["end_ms"], interval[1]) - max(segment["start_ms"], interval[0]),
    )


def _participant_for_segment(segment, labels, intervals):
    ranked = sorted(
        (
            (
                participant_ref,
                sum(_overlap_ms(segment, interval) for interval in speech),
            )
            for participant_ref, speech in intervals.items()
            if participant_ref in labels
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] <= 0:
        return None
    if len(ranked) > 1 and ranked[0][1] <= ranked[1][1]:
        return None
    return labels[ranked[0][0]]


def _speaker_participant_mapping(transcript, labels, intervals):
    acoustic_refs = {
        segment["speaker"]["ref"]
        for segment in transcript["segments"]
        if segment.get("speaker", {}).get("kind") == "acoustic"
    }
    if not intervals:
        if len(acoustic_refs) == 1 and len(set(labels.values())) == 1:
            return {next(iter(acoustic_refs)): next(iter(labels.values()))}
        return {}

    scores = {speaker_ref: {} for speaker_ref in acoustic_refs}
    for segment in transcript["segments"]:
        speaker_ref = segment.get("speaker", {}).get("ref")
        if speaker_ref not in scores:
            continue
        for participant_ref, speech in intervals.items():
            scores[speaker_ref][participant_ref] = sum(
                _overlap_ms(segment, interval) for interval in speech
            )

    mapping = {}
    for speaker_ref, participant_scores in scores.items():
        ranked = sorted(
            participant_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] <= 0:
            continue
        if len(ranked) > 1 and ranked[0][1] <= ranked[1][1]:
            continue
        participant_ref = ranked[0][0]
        if participant_ref in labels:
            mapping[speaker_ref] = labels[participant_ref]
    return mapping


def _has_labeled_overlap(segment, labels, intervals):
    return any(
        participant_ref in labels
        and any(_overlap_ms(segment, interval) > 0 for interval in speech)
        for participant_ref, speech in intervals.items()
    )


def map_speakers(transcript, speaker_evidence=None):
    """Use unambiguous VAD overlap; otherwise retain stable anonymous speakers."""

    labels = _participant_labels(speaker_evidence or {})
    intervals = _speech_intervals(speaker_evidence or {})
    speaker_mapping = _speaker_participant_mapping(transcript, labels, intervals)
    anonymous = {}
    for segment in transcript["segments"]:
        label = _participant_for_segment(segment, labels, intervals)
        if label is None and not _has_labeled_overlap(segment, labels, intervals):
            label = speaker_mapping.get(segment["speaker"]["ref"])
        if label is not None:
            segment["speaker"] = {"kind": "participant", "label": label}
            continue
        speaker_ref = segment["speaker"]["ref"]
        index = anonymous.setdefault(speaker_ref, len(anonymous) + 1)
        segment["speaker"] = {"kind": "anonymous", "index": index}
    return transcript


def canonical_transcript(transcription_ref, transcript):
    """Shape the citation-anchor artifact independent of provider metadata."""

    artifact = {
        "version": ARTIFACT_SCHEMA_VERSION,
        "transcription_ref": transcription_ref,
        "segments": [
            {
                "segment_id": segment["segment_id"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "speaker": segment["speaker"],
                "text": segment["text"],
                **(
                    {"confidence": segment["confidence"]}
                    if segment.get("confidence") is not None
                    else {}
                ),
            }
            for segment in transcript["segments"]
        ],
    }
    if isinstance(transcript.get("language"), str):
        artifact["language"] = transcript["language"]
    return artifact
