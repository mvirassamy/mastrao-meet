"""Tests for the bounded PostgreSQL subtitle convergence lock."""

from contextlib import nullcontext
from unittest import mock

from django.db import connection, transaction

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from core.models import RoomSubtitleAdvisoryKey
from core.services.subtitle_lock import (
    SubtitleLockUnavailable,
    SubtitleLockUsageError,
    _connection_parameters,
    resolve_subtitle_lock_key,
    subtitle_lock_metrics,
    try_subtitle_convergence_lock,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_registry_assigns_one_exact_key_per_room_sid():
    """The registry is stable for one SID and distinct for another SID."""

    first = resolve_subtitle_lock_key("RM_lock_registry_a")
    assert resolve_subtitle_lock_key("RM_lock_registry_a") == first
    second = resolve_subtitle_lock_key("RM_lock_registry_b")

    assert second != first
    assert RoomSubtitleAdvisoryKey.objects.count() == 2


def test_lock_refuses_to_run_inside_a_django_transaction():
    """Provider calls cannot accidentally run under a row-lock transaction."""

    with transaction.atomic():
        with pytest.raises(SubtitleLockUsageError):
            with try_subtitle_convergence_lock("RM_lock_atomic"):
                pass


def test_lock_timeout_is_bounded_and_release_is_confirmed(settings):
    """A held room lock reports busy, then becomes reusable after release."""

    settings.ROOM_SUBTITLE_LOCK_TIMEOUT_SECONDS = 0.05
    with try_subtitle_convergence_lock("RM_lock_busy") as first:
        assert first is not None
        with try_subtitle_convergence_lock("RM_lock_busy", timeout=0.02) as second:
            assert second is None

    with try_subtitle_convergence_lock("RM_lock_busy", timeout=0.1) as released:
        assert released is not None


def test_provider_errors_are_not_reclassified_as_lock_contention():
    """The caller's exception crosses the lock context unchanged."""

    marker = RuntimeError("provider failed")
    with pytest.raises(RuntimeError, match="provider failed"):
        with try_subtitle_convergence_lock("RM_lock_error") as lock:
            assert lock is not None
            raise marker

    with try_subtitle_convergence_lock("RM_lock_error", timeout=0.1) as released:
        assert released is not None


def test_default_lock_route_keeps_django_connection_parameters(settings):
    """Without an explicit URL the existing Django route remains unchanged."""

    settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = ""
    original = {"host": "default.example", "dbname": "meet", "context": object()}
    with mock.patch.object(connection, "get_connection_params", return_value=original):
        assert _connection_parameters() == {
            "host": "default.example",
            "dbname": "meet",
        }
    assert "context" in original


def test_direct_lock_route_does_not_inherit_the_default_route(settings):
    """The direct URL supplies its own identity and TLS settings."""

    settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = (
        "postgresql://lock:direct-pass@direct.example:5433/meet?sslmode=verify-full"
    )
    with mock.patch.object(connection, "get_connection_params") as default_parameters:
        assert _connection_parameters() == {
            "user": "lock",
            "password": "direct-pass",
            "host": "direct.example",
            "port": "5433",
            "dbname": "meet",
            "sslmode": "verify-full",
        }
    default_parameters.assert_not_called()


@pytest.mark.parametrize("direct_url", [False, True])
@pytest.mark.parametrize("provider_error", [False, True])
def test_acquire_and_unlock_use_one_postgres_session(
    settings, monkeypatch, direct_url, provider_error
):
    """Real PostgreSQL acquisition and unlock share one dedicated backend PID."""

    room_sid = "RM_lock_session"
    resolve_subtitle_lock_key(room_sid)
    settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = ""
    if direct_url:
        settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = make_conninfo(
            connection.connection.info.dsn,
            password=connection.settings_dict["PASSWORD"],
            application_name="meet-db2-direct-test",
        )

    statements = []
    execute = psycopg.Cursor.execute

    def record_execute(cursor, query, params=None, **kwargs):
        result = execute(cursor, query, params, **kwargs)
        if query in (
            "SELECT pg_try_advisory_lock(%s, %s)",
            "SELECT pg_advisory_unlock(%s, %s)",
        ):
            statements.append((query, cursor.connection.info.backend_pid))
        return result

    monkeypatch.setattr(psycopg.Cursor, "execute", record_execute)
    unlocks_before = subtitle_lock_metrics().get("subtitle_lock_unlock_total:ok", 0)
    outcome = nullcontext()
    if provider_error:
        outcome = pytest.raises(RuntimeError, match="provider failed")

    with mock.patch(
        "core.services.subtitle_lock.psycopg.connect", wraps=psycopg.connect
    ) as connect_session:
        with outcome:
            with try_subtitle_convergence_lock(room_sid) as lock:
                assert lock is not None
                lock_pid = statements[0][1]
                assert lock_pid != connection.connection.info.backend_pid
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pid FROM pg_locks WHERE locktype = 'advisory' "
                        "AND pid = %s AND classid = %s AND objid = %s "
                        "AND objsubid = 2 AND granted",
                        [
                            lock_pid,
                            settings.ROOM_SUBTITLE_LOCK_NAMESPACE,
                            lock.lock_key,
                        ],
                    )
                    assert cursor.fetchall() == [(lock_pid,)]
                if provider_error:
                    raise RuntimeError("provider failed")

    assert connect_session.call_count == 1
    assert connect_session.call_args.kwargs["autocommit"] is True
    if direct_url:
        assert (
            connect_session.call_args.kwargs["application_name"]
            == "meet-db2-direct-test"
        )
    assert statements == [
        ("SELECT pg_try_advisory_lock(%s, %s)", lock_pid),
        ("SELECT pg_advisory_unlock(%s, %s)", lock_pid),
    ]
    assert (
        subtitle_lock_metrics()["subtitle_lock_unlock_total:ok"] == unlocks_before + 1
    )


def test_invalid_direct_url_does_not_fall_back_to_default(settings):
    """An invalid explicit route fails without opening the default route."""

    resolve_subtitle_lock_key("RM_lock_invalid_url")
    settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = "not-a-connection-string"
    with mock.patch("core.services.subtitle_lock.psycopg.connect") as connect_session:
        with pytest.raises(SubtitleLockUnavailable):
            with try_subtitle_convergence_lock("RM_lock_invalid_url"):
                pytest.fail("invalid direct route must not acquire a lock")
    connect_session.assert_not_called()


def test_unavailable_direct_route_does_not_fall_back_to_default(settings):
    """A failed direct connection is attempted once without retrying Django."""

    resolve_subtitle_lock_key("RM_lock_unavailable")
    settings.ROOM_SUBTITLE_LOCK_DATABASE_URL = "postgresql://direct.example/meet"
    with mock.patch(
        "core.services.subtitle_lock.psycopg.connect",
        side_effect=psycopg.OperationalError("direct route unavailable"),
    ) as connect_session:
        with pytest.raises(SubtitleLockUnavailable):
            with try_subtitle_convergence_lock("RM_lock_unavailable"):
                pytest.fail("unavailable direct route must not acquire a lock")
    connect_session.assert_called_once_with(
        host="direct.example",
        dbname="meet",
        connect_timeout=2,
        autocommit=True,
    )
