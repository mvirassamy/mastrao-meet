"""Optional exact Platform #201 verifier qualification, without its runtime ledger."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import
# pylint: disable=too-many-arguments,too-many-positional-arguments

import json
import os
import subprocess
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.tests.test_mastrao_media_token_binding import (
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_rtc_admissions import _issued, peer
from core.tests.test_mastrao_rtc_correlation import _join
from core.tests.test_mastrao_rtc_observations import _post

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not os.environ.get("MEET_CORE_CONTRACT_ROOT"),
        reason="explicit local Platform #201 checkout required",
    ),
]


def test_pinned_core_verifies_actual_meet_assertions(  # noqa: PLR0913,PLR0917
    client, settings, host, guest, peer, tmp_path
):
    root = Path(os.environ["MEET_CORE_CONTRACT_ROOT"])
    # The author's checkout may advance; require the verifier and its schemas
    # to remain byte-identical to the agreed revision without copying its tree.
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "4c3668237e3a4cddb42ec2f5c45083f15cf1ac95",
            "--",
            "packages/meeting-runtime-contract/src",
            "services/cabinet-core/src/meetings/meeting-start-security.ts",
            "services/cabinet-core/src/errors.ts",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    for grant, sid in [(host, "PA_host"), (guest, "PA_guest")]:
        assert _post(client, settings, _join(_issued(grant), sid)).status_code == 200
    private_jwk = json.loads(settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK)

    public = (
        Ed25519PrivateKey.from_private_bytes(
            jwt.utils.base64url_decode(private_jwk["d"])
        )
        .public_key()
        .public_bytes_raw()
    )
    receipt = {
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "keyId": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
        "publicJwk": {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": jwt.utils.base64url_encode(public).decode(),
        },
    }
    signing = {
        "issuer": settings.MASTRAO_ROOM_EFFECT_ISSUER,
        "audience": settings.MASTRAO_ROOM_EFFECT_AUDIENCE,
        "keyId": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "publicJwk": json.loads(settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK),
    }
    script = tmp_path / "verify.mts"
    module = root / "services/cabinet-core/src/meetings/meeting-start-security.ts"
    script.write_text(f"""
import {{ readFileSync }} from 'node:fs';
import {{ MeetingStartSecurity }} from {json.dumps(module.as_uri())};
const input = JSON.parse(readFileSync(0, 'utf8'));
const verifier = new MeetingStartSecurity(input.config, () => Math.floor(Date.now() / 1000));
for (const envelope of input.envelopes) {{
  const verified = await verifier.verifyObservation(envelope);
  if (!['host', 'guest'].includes(verified.participantAuthority.kind)) {{
    throw Error('unexpected authority');
  }}
}}
process.stdout.write('host and guest assertions verified\\n');
""")
    result = subprocess.run(
        [str(root / "node_modules/.bin/tsx"), "--conditions=development", str(script)],
        cwd=root,
        input=json.dumps(
            {"config": {"receipt": receipt, "signing": signing}, "envelopes": peer[1]}
        ),
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == "host and guest assertions verified"
