"""Regression tests for database settings."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "direct_url",
    [None, "postgresql://lock:pass@direct.example/meet?sslmode=require"],
)
@pytest.mark.parametrize(
    "database_environment",
    [
        {"DATABASE_URL": "postgresql://meet:pass@postgres.example:5432/meet"},
        {
            "DB_HOST": "postgres.example",
            "DB_NAME": "meet",
            "DB_USER": "meet",
            "DB_PASSWORD": "pass",
            "DB_PORT": "5432",
        },
    ],
)
def test_database_configuration_is_safe_for_transaction_pooling(
    database_environment, direct_url
):
    """Both database configuration paths remain safe for transaction pooling."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        **database_environment,
    }
    environment.pop("ROOM_SUBTITLE_LOCK_DATABASE_URL", None)
    if direct_url is not None:
        environment["ROOM_SUBTITLE_LOCK_DATABASE_URL"] = direct_url
    if "DATABASE_URL" not in database_environment:
        environment.pop("DATABASE_URL", None)

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "from django.db import connections; "
                "database = settings.DATABASES['default']; "
                "assert len(settings.DATABASES) == 1; "
                "assert database['HOST'] == 'postgres.example'; "
                "assert database['NAME'] == 'meet'; "
                "assert database['USER'] == 'meet'; "
                "assert str(database['PORT']) == '5432'; "
                "assert settings.ROOM_SUBTITLE_LOCK_DATABASE_URL "
                "== os.environ.get('ROOM_SUBTITLE_LOCK_DATABASE_URL', ''); "
                "assert database['DISABLE_SERVER_SIDE_CURSORS']; "
                "assert connections['default'].get_connection_params()['prepare_threshold'] is None"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )
