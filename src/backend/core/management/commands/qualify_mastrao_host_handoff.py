"""Run the provider-free browser handoff against the real Cabinet Core endpoint."""

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner
from django.urls import reverse

from core.mastrao_host_grant import SESSION_NONCE_KEY
from core.mastrao_room_binding import ensure_room
from core.models import MastraoHostGrant, MastraoHostIdentity, ResourceAccess


class Command(BaseCommand):
    """Consume one real Core handoff into one Meet session-bound grant."""

    help = "Qualify the Mastrao host handoff with a local signed vector"

    def add_arguments(self, parser):
        parser.add_argument("vector_path")

    def handle(self, *args, **options):
        try:
            vector = json.loads(
                Path(options["vector_path"]).read_text(encoding="utf-8")
            )
            configuration = vector["configuration"]
            room_effect = vector["room_effect"]
            host_handoff = vector["host_handoff"]
            result_path = Path(vector["result_path"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError("Invalid host qualification vector") from error

        runner = DiscoverRunner(
            verbosity=0,
            interactive=False,
            keepdb=os.environ.get("MASTRAO_QUALIFICATION_KEEP_DATABASE") == "1",
        )
        previous_databases = runner.setup_databases()
        try:
            self._qualify(
                configuration,
                room_effect,
                host_handoff,
                result_path,
            )
        finally:
            runner.teardown_databases(previous_databases)

    def _qualify(self, configuration, room_effect, host_handoff, result_path):
        ensure_room(room_effect)
        with override_settings(
            MASTRAO_HOST_HANDOFF_ENABLED=True,
            MASTRAO_PLATFORM_ORIGIN=configuration["platform_origin"],
            MASTRAO_CORE_REDEMPTION_ENDPOINT=configuration["core_redemption_endpoint"],
            MASTRAO_CORE_REDEMPTION_TIMEOUT_SECONDS=10,
            MASTRAO_ROOM_EFFECT_ISSUER=configuration["effect_issuer"],
            MASTRAO_ROOM_EFFECT_AUDIENCE=configuration["effect_audience"],
            MASTRAO_ROOM_EFFECT_PUBLIC_JWK=json.dumps(
                configuration["effect_public_jwk"]
            ),
            MASTRAO_ROOM_EFFECT_KEY_ID=configuration["effect_key_id"],
            MASTRAO_ROOM_RECEIPT_ISSUER=configuration["receipt_issuer"],
            MASTRAO_ROOM_RECEIPT_AUDIENCE=configuration["receipt_audience"],
            MASTRAO_ROOM_RECEIPT_PRIVATE_JWK=json.dumps(
                configuration["receipt_private_jwk"]
            ),
            MASTRAO_ROOM_RECEIPT_KEY_ID=configuration["receipt_key_id"],
        ):
            client = Client()
            response = client.post(
                reverse("consume_mastrao_host_handoff"),
                data=f"host_handoff={host_handoff}",
                content_type="application/x-www-form-urlencoded",
                HTTP_ORIGIN=configuration["platform_origin"],
                HTTP_SEC_FETCH_SITE="cross-site",
            )
            if response.status_code != 303 or SESSION_NONCE_KEY not in client.session:
                raise CommandError(
                    "Host handoff refused qualification vector "
                    f"(status={response.status_code})"
                )

        valid = (
            MastraoHostIdentity.objects.count() == 1
            and MastraoHostGrant.objects.count() == 1
            and ResourceAccess.objects.count() == 1
        )
        if not valid:
            raise CommandError("Host handoff created durable room authority")
        result_path.write_text(
            json.dumps(
                {
                    "location": response["Location"],
                    "host_grants": MastraoHostGrant.objects.count(),
                    "resource_accesses": ResourceAccess.objects.count(),
                }
            ),
            encoding="utf-8",
        )
        os.chmod(result_path, 0o600)
        self.stdout.write("Mastrao host handoff qualification passed")
