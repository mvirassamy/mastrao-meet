"""Explicit local SFU qualification, separate from SDK-signed synthetic fixtures.

Uses an actual LiveKit server and RTC connections. The Core response peer remains
synthetic; this does not claim deployed Core ACL/ledger or staging qualification.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import
# pylint: disable=too-many-locals,too-many-statements,too-many-arguments
# pylint: disable=too-many-positional-arguments,import-outside-toplevel,no-member

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import Client

import pytest
from livekit import api

from core import models
from core.tests.test_mastrao_media_token_binding import (
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_rtc_admissions import _issued, peer

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("MEET_RTC_QUALIFICATION") != "local",
        reason="explicit local LiveKit SFU qualification only",
    ),
]


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_real_sfu_host_guest_service_and_leave_rejoin(  # noqa: PLR0915
    settings, host, guest, peer, tmp_path
):
    rtc = pytest.importorskip(
        "livekit.rtc", reason="local qualification RTC SDK required"
    )

    executable = shutil.which("livekit-server")
    assert executable, "Local LiveKit server required"
    receipts = []
    failures = []
    credentials = settings.LIVEKIT_CONFIGURATION

    class WebhookHandler(BaseHTTPRequestHandler):
        """Receive unmodified webhooks from the actual local SFU."""

        def do_POST(self):  # pylint: disable=invalid-name
            body = self.rfile.read(int(self.headers["Content-Length"]))
            # Real server-issued authorization and exact raw bytes, never re-signed.
            response = Client().post(
                "/api/v1.0/rooms/webhooks-livekit/",
                data=body,
                content_type="application/json",
                HTTP_AUTHORIZATION=self.headers["Authorization"],
            )
            if response.status_code != 200:
                failures.append(response.status_code)
            event = json.loads(body)
            receipts.append(
                (event["event"], event["id"], hashlib.sha256(body).hexdigest())
            )
            self.send_response(response.status_code)
            self.end_headers()

        def log_message(self, *_args):
            """Keep raw webhook/token/identity information out of test logs."""

    webhook = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
    thread = threading.Thread(target=webhook.serve_forever, daemon=True)
    thread.start()
    sf_port, tcp_port, udp_port = _free_port(), _free_port(), _free_port()
    config = tmp_path / "livekit.yaml"
    config.write_text(
        f"port: {sf_port}\nbind_addresses: [127.0.0.1]\n"
        f"rtc:\n  tcp_port: {tcp_port}\n  udp_port: {udp_port}\n  use_external_ip: false\n"
        f"keys:\n  {credentials['api_key']}: {credentials['api_secret']}\n"
        f"webhook:\n  api_key: {credentials['api_key']}\n"
        f"  urls: [http://127.0.0.1:{webhook.server_port}/]\n"
        "logging:\n  level: error\n"
    )
    with (tmp_path / "server.log").open("w") as log:
        server = subprocess.Popen(  # pylint: disable=consider-using-with
            [executable, "--config", str(config), "--node-ip", "127.0.0.1"],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                assert server.poll() is None, (
                    "LiveKit startup failed; inspect local server.log"
                )
                try:
                    with socket.create_connection(("127.0.0.1", sf_port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("Local SFU did not listen")

            def token_for(issued, kind="standard"):
                return (
                    api.AccessToken(credentials["api_key"], credentials["api_secret"])
                    .with_identity("service_fixture")
                    .with_kind(kind)
                    .with_attributes(
                        {"mastrao.media_token_binding_ref": str(issued.pk)}
                    )
                    .with_grants(
                        api.VideoGrants(
                            room_join=True,
                            room=str(issued.room_binding.room_id),
                            can_publish=False,
                            can_update_own_metadata=False,
                        )
                    )
                    .to_jwt()
                )

            host_token, host_config = _issued(host, with_config=True)
            _, guest_config = _issued(guest, with_config=True)
            _, rejoin_config = _issued(host, with_config=True)
            # The SFU tokens below preserve the actual production-issued binding.
            # The Core authorities and local issuance rows are real signed fixtures.
            participants = [
                host_config["token"],
                guest_config["token"],
                token_for(host_token, "agent"),
                rejoin_config["token"],
            ]

            async def connect_and_leave(token):
                room = rtc.Room()
                try:
                    await room.connect(
                        f"ws://127.0.0.1:{sf_port}",
                        token,
                        rtc.RoomOptions(auto_subscribe=False, connect_timeout=10),
                    )
                    await asyncio.sleep(0.3)
                finally:
                    await room.disconnect()

            for token in participants:
                asyncio.run(connect_and_leave(token))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if (
                    models.MastraoRtcObservation.objects.filter(
                        event_type="participant_joined"
                    ).count()
                    >= 4
                ):
                    break
                time.sleep(0.05)
            joins = models.MastraoRtcObservation.objects.filter(
                event_type="participant_joined"
            )
            assert joins.count() == 4
            assert not failures
            humans = joins.filter(participant_kind=api.ParticipantInfo.STANDARD)
            assert humans.count() == 3
            assert all(row.admission_receipt is not None for row in humans)
            assert (
                joins.get(participant_kind=api.ParticipantInfo.AGENT).admission_receipt
                is None
            )
            assert {item["authority_kind"] for item in peer[2]} == {"host", "guest"}
            starts = list(humans.values_list("admission_receipt", flat=True))
            assert len({item["first_started_at"] for item in starts}) == 1
            verified_digests = {
                event_id: digest
                for event_type, event_id, digest in receipts
                if event_type == "participant_joined"
            }
            assert all(
                row.payload_digest == verified_digests[row.event_id] for row in joins
            )
        finally:
            server.terminate()
            server.wait(timeout=10)
            webhook.shutdown()
            webhook.server_close()
            thread.join(timeout=5)
