"""Regression tests for database settings."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "database_environment",
    [
        {"DATABASE_URL": "postgresql://meet:pass@postgres.example/meet"},
        {
            "DB_HOST": "postgres.example",
            "DB_NAME": "meet",
            "DB_USER": "meet",
            "DB_PASSWORD": "pass",
            "DB_PORT": "5432",
        },
    ],
)
def test_database_configuration_is_safe_for_transaction_pooling(database_environment):
    """Both database configuration paths remain safe for transaction pooling."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        **database_environment,
    }
    if "DATABASE_URL" not in database_environment:
        environment.pop("DATABASE_URL", None)

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "from django.db import connections; "
                "database = settings.DATABASES['default']; "
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
