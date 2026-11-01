"""Celery tasks for the core app."""

from core.tasks.connection_test import delete_connection_test_room
from core.tasks.file import process_file_deletion
from core.tasks.room_audio import (
    process_room_audio_admissions,
    process_room_audio_asr,
    process_room_audio_sources,
)

__all__ = (
    "delete_connection_test_room",
    "process_file_deletion",
    "process_room_audio_admissions",
    "process_room_audio_asr",
    "process_room_audio_sources",
)
