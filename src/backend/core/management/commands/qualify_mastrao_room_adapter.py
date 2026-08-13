"""Run the provider-free cross-repository Mastrao room qualification vector."""

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner
from django.urls import reverse

from core.models import MastraoRoomBinding, ResourceAccess, RoleChoices, Room


class Command(BaseCommand):
    """Consume two signed claims and persist the converged receipt."""

    help = "Qualify the Mastrao room adapter with a local signed vector"

    def add_arguments(self, parser):
        parser.add_argument("vector_path")

    def handle(self, *args, **options):
        vector_path = Path(options["vector_path"])
        try:
            vector = json.loads(vector_path.read_text(encoding="utf-8"))
            configuration = vector["configuration"]
            room_effects = vector["room_effects"]
            result_path = Path(vector["result_path"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError("Invalid qualification vector") from error
        if not isinstance(room_effects, list) or len(room_effects) != 2:
            raise CommandError("Qualification requires exactly two room effects")

        runner = DiscoverRunner(
            verbosity=0,
            interactive=False,
            keepdb=os.environ.get("MASTRAO_QUALIFICATION_KEEP_DATABASE") == "1",
        )
        previous_databases = runner.setup_databases()
        try:
            self._qualify(configuration, room_effects, result_path)
        finally:
            runner.teardown_databases(previous_databases)

    def _qualify(self, configuration, room_effects, result_path):

        with override_settings(
            MASTRAO_ROOM_ADAPTER_ENABLED=True,
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
            receipts = []
            for room_effect in room_effects:
                response = client.post(
                    reverse("ensure_mastrao_room"),
                    data=json.dumps({"room_effect": room_effect}),
                    content_type="application/json",
                )
                if response.status_code != 200:
                    raise CommandError("Room adapter refused qualification vector")
                receipts.append(response.json()["room_receipt"])

        binding = MastraoRoomBinding.objects.select_related("room", "owner").get()
        valid_binding = (
            ResourceAccess.objects.filter(
                resource=binding.room,
                user=binding.owner,
                role=RoleChoices.OWNER,
            ).count()
            == 1
            and MastraoRoomBinding.objects.count() == 1
            and Room.objects.count() == 1
        )
        if not valid_binding:
            raise CommandError("Room adapter did not converge idempotently")
        result_path.write_text(
            json.dumps({"room_receipt": receipts[-1]}), encoding="utf-8"
        )
        os.chmod(result_path, 0o600)
        self.stdout.write("Mastrao room adapter qualification passed")
