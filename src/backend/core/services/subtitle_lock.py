"""Bounded PostgreSQL session locks for subtitle provider convergence."""
# pylint: disable=no-member

import threading
import time
from collections import Counter
from contextlib import contextmanager
from logging import getLogger

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction

import psycopg

from core.models import RoomSubtitleAdvisoryKey

logger = getLogger(__name__)

_SEQUENCE_NAME = "meet_room_subtitle_advisory_key_seq"
_MAX_LOCK_KEY = 2_147_483_647
_GATE_CONDITION = threading.Condition()
_GATE_STATE = {"in_use": 0}
_METRICS = Counter()


class SubtitleLockUsageError(RuntimeError):
    """The provider lock was requested from inside a Django transaction."""


class SubtitleLockUnavailable(RuntimeError):
    """The dedicated PostgreSQL session could not be created."""


class SubtitleConvergenceLock:
    """Metadata for an acquired room-scoped provider lock."""

    def __init__(self, room_sid, lock_key, acquired_at):
        self.room_sid = room_sid
        self.lock_key = lock_key
        self.acquired_at = acquired_at


def _metric(name, outcome=None):
    key = name if outcome is None else f"{name}:{outcome}"
    with _GATE_CONDITION:
        _METRICS[key] += 1


def subtitle_lock_metrics():
    """Return bounded process-local lock metrics for diagnostics and tests."""

    with _GATE_CONDITION:
        return dict(_METRICS)


def _next_lock_key():
    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval(%s)", [_SEQUENCE_NAME])
        lock_key = int(cursor.fetchone()[0])
    if lock_key > _MAX_LOCK_KEY:
        raise SubtitleLockUnavailable("Subtitle advisory key sequence is exhausted.")
    return lock_key


def resolve_subtitle_lock_key(room_sid):
    """Return the permanent exact integer key for a LiveKit room SID."""

    try:
        return RoomSubtitleAdvisoryKey.objects.get(room_sid=room_sid).lock_key
    except RoomSubtitleAdvisoryKey.DoesNotExist:
        lock_key = _next_lock_key()
        try:
            with transaction.atomic():
                RoomSubtitleAdvisoryKey.objects.create(
                    room_sid=room_sid,
                    lock_key=lock_key,
                )
        except (IntegrityError, ValidationError):
            pass
        return RoomSubtitleAdvisoryKey.objects.get(room_sid=room_sid).lock_key


def _gate_acquire(deadline):
    with _GATE_CONDITION:
        while _GATE_STATE["in_use"] >= settings.ROOM_SUBTITLE_LOCK_MAX_CONNECTIONS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _GATE_CONDITION.wait(timeout=remaining)
        _GATE_STATE["in_use"] += 1
        return True


def _gate_release():
    with _GATE_CONDITION:
        _GATE_STATE["in_use"] -= 1
        _GATE_CONDITION.notify()


def _connection_parameters():
    connection = connections["default"]
    parameters = connection.get_connection_params().copy()
    parameters.pop("context", None)
    return parameters


def _set_statement_timeout(connection):
    timeout_ms = max(
        1,
        int(settings.ROOM_SUBTITLE_LOCK_STATEMENT_TIMEOUT_SECONDS * 1000),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            [str(timeout_ms)],
        )


@contextmanager
def try_subtitle_convergence_lock(  # noqa: PLR0912, PLR0915  # pylint: disable=too-many-branches,too-many-statements
    room_sid,
    *,
    timeout=None,
):
    """Try a dedicated session advisory lock without holding DB row locks."""

    default_connection = connections["default"]
    if default_connection.in_atomic_block:
        raise SubtitleLockUsageError(
            "Subtitle provider convergence cannot run inside a DB transaction."
        )

    timeout = (
        settings.ROOM_SUBTITLE_LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    )
    deadline = time.monotonic() + max(0.0, timeout)
    if not _gate_acquire(deadline):
        _metric("subtitle_lock_acquisition_total", "busy")
        yield None
        return

    connection = None
    cursor = None
    locked = False
    acquired_at = None
    lock_key = None
    try:
        try:
            lock_key = resolve_subtitle_lock_key(room_sid)
            parameters = _connection_parameters()
            parameters["connect_timeout"] = max(
                1, int(settings.ROOM_SUBTITLE_LOCK_CONNECTION_TIMEOUT_SECONDS)
            )
            connection = psycopg.connect(**parameters, autocommit=True)  # pylint: disable=no-member
            _set_statement_timeout(connection)
            cursor = connection.cursor()
            namespace = int(settings.ROOM_SUBTITLE_LOCK_NAMESPACE)
            while True:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    [namespace, lock_key],
                )
                locked = bool(cursor.fetchone()[0])
                if locked:
                    acquired_at = time.monotonic()
                    _metric("subtitle_lock_acquisition_total", "acquired")
                    _metric("subtitle_lock_hold_seconds")
                    break
                if time.monotonic() >= deadline:
                    _metric("subtitle_lock_acquisition_total", "timeout")
                    break
                time.sleep(min(0.01, max(0.001, deadline - time.monotonic())))
        except (psycopg.Error, OSError) as error:
            _metric("subtitle_lock_acquisition_total", "error")
            raise SubtitleLockUnavailable(
                "Subtitle advisory lock session failed"
            ) from error

        if not locked:
            yield None
            return

        yield SubtitleConvergenceLock(room_sid, lock_key, acquired_at)
        return
    finally:
        if cursor is not None and locked:
            try:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [int(settings.ROOM_SUBTITLE_LOCK_NAMESPACE), lock_key],
                )
                if not cursor.fetchone()[0]:
                    _metric("subtitle_lock_unlock_total", "failed")
                    logger.error("Subtitle advisory unlock was not confirmed")
                else:
                    _metric("subtitle_lock_unlock_total", "ok")
            except Exception:  # pylint: disable=broad-exception-caught
                _metric("subtitle_lock_unlock_total", "error")
                logger.exception("Failed to release subtitle advisory lock")
        if acquired_at is not None:
            with _GATE_CONDITION:
                _METRICS["subtitle_lock_hold_seconds_total"] += max(
                    0, int((time.monotonic() - acquired_at) * 1000)
                )
        try:
            if cursor is not None:
                cursor.close()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to close subtitle advisory cursor")
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to close subtitle advisory connection")
            finally:
                _gate_release()
