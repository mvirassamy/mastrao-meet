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
    """Both routes keep pooling-safe defaults and redact the lock URL."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "DJANGO_SECRET_KEY": "synthetic-settings-report-key",
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
                "from django.views.debug import get_default_exception_reporter_filter; "
                "database = settings.DATABASES['default']; "
                "assert len(settings.DATABASES) == 1; "
                "assert database['HOST'] == 'postgres.example'; "
                "assert database['NAME'] == 'meet'; "
                "assert database['USER'] == 'meet'; "
                "assert str(database['PORT']) == '5432'; "
                "assert settings.ROOM_SUBTITLE_LOCK_DATABASE_URL "
                "== os.environ.get('ROOM_SUBTITLE_LOCK_DATABASE_URL', ''); "
                "assert database['DISABLE_SERVER_SIDE_CURSORS']; "
                "assert connections['default'].get_connection_params()"
                "['prepare_threshold'] is None; "
                "settings.DEBUG = False; "
                "report_filter = get_default_exception_reporter_filter(); "
                "safe_settings = report_filter.get_safe_settings(); "
                "assert safe_settings['ROOM_SUBTITLE_LOCK_DATABASE_URL'] "
                "== report_filter.cleansed_substitute; "
                "assert safe_settings['DATABASES']['default']['PASSWORD'] "
                "== report_filter.cleansed_substitute; "
                "assert report_filter.cleanse_setting('ORDINARY_SETTING', 'visible') == 'visible'"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )
