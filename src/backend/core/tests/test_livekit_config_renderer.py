"""Exercise the dedicated LiveKit and Egress configuration renderer."""

import json
import stat
from pathlib import Path
from unittest import mock

import pytest

from livekit_config_renderer import render


def common_arguments(directory: Path) -> list[str]:
    """Create mounted secret fixtures and return shared renderer arguments."""
    secrets = {
        "api-key": "livekit-key",
        "api-secret": "livekit-secret",
        "redis-username": "redis-user",
        "redis-password": "redis-password",
        "redis-ca": "synthetic CA",
    }
    paths = {}
    for name, value in secrets.items():
        path = directory / name
        path.write_text(value, encoding="utf-8")
        paths[name] = path
    return [
        "--api-key-file",
        str(paths["api-key"]),
        "--api-secret-file",
        str(paths["api-secret"]),
        "--redis-username-file",
        str(paths["redis-username"]),
        "--redis-password-file",
        str(paths["redis-password"]),
        "--redis-host",
        "redis.internal.example",
        "--redis-port",
        "6379",
        "--redis-db",
        "0",
        "--redis-ca-file",
        str(paths["redis-ca"]),
        "--redis-server-name",
        "redis.internal.example",
        "--redis-timeout-seconds",
        "5",
        "--log-level",
        "warn",
        "--json-logging",
        "true",
        "--output",
        str(directory / "rendered.yaml"),
    ]


def livekit_arguments(directory: Path) -> list[str]:
    """Return a complete synthetic LiveKit renderer invocation."""
    return [
        "livekit",
        *common_arguments(directory),
        "--port",
        "7880",
        "--bind-address",
        "127.0.0.1",
        "--bind-address",
        "10.0.0.4",
        "--development",
        "false",
        "--rtc-tcp-port",
        "7881",
        "--rtc-udp-port",
        "7882",
        "--rtc-ip-include",
        "10.0.0.4/32",
        "--rtc-ip-include",
        "203.0.113.9/32",
        "--force-tcp",
        "false",
        "--use-external-ip",
        "false",
        "--advertise-internal-ip",
        "true",
        "--enable-loopback-candidate",
        "false",
        "--node-ip",
        "203.0.113.9",
        "--no-stun-servers",
        "--auto-create-rooms",
        "false",
        "--webhook-url",
        "http://meet-api.internal/api/v1.0/rooms/webhooks-livekit/",
    ]


def egress_arguments(directory: Path) -> list[str]:
    """Return a complete synthetic Egress renderer invocation."""
    return [
        "egress",
        *common_arguments(directory),
        "--ws-url",
        "ws://livekit-signal.internal:7880",
        "--health-port",
        "9090",
        "--disallow-local-storage",
        "false",
        "--enable-chrome-sandbox",
        "false",
        "--track-composite-cpu-cost",
        "1.0",
        "--file-output-max-duration",
        "10m",
        "--segment-output-max-duration",
        "10m",
    ]


def successful_redis():
    """Return a patched Redis client whose TLS health probe succeeds."""
    client = mock.Mock()
    client.ping.return_value = True
    return mock.patch(
        "livekit_config_renderer.redis.Redis", return_value=client
    ), client


def test_livekit_mode_verifies_redis_tls_and_renders_exact_config(tmp_path):
    """Render the exact LiveKit config only after a successful Redis probe."""
    redis_patch, client = successful_redis()
    with redis_patch as redis_constructor:
        output = render(livekit_arguments(tmp_path))

    assert output == tmp_path / "rendered.yaml"
    config = json.loads(output.read_text(encoding="utf-8"))
    assert config == {
        "bind_addresses": ["127.0.0.1", "10.0.0.4"],
        "development": False,
        "keys": {"livekit-key": "livekit-secret"},
        "logging": {"json": True, "level": "warn"},
        "port": 7880,
        "redis": {
            "address": "redis.internal.example:6379",
            "db": 0,
            "password": "redis-password",
            "tls": {
                "ca_cert_file": str(tmp_path / "redis-ca"),
                "enabled": True,
                "insecure": False,
                "server_name": "redis.internal.example",
            },
            "username": "redis-user",
        },
        "room": {"auto_create": False},
        "rtc": {
            "advertise_internal_ip": True,
            "enable_loopback_candidate": False,
            "force_tcp": False,
            "ips": {"includes": ["10.0.0.4/32", "203.0.113.9/32"]},
            "node_ip": "203.0.113.9",
            "stun_servers": [],
            "tcp_port": 7881,
            "udp_port": 7882,
            "use_external_ip": False,
        },
        "webhook": {
            "api_key": "livekit-key",
            "urls": ["http://meet-api.internal/api/v1.0/rooms/webhooks-livekit/"],
        },
    }
    redis_constructor.assert_called_once_with(
        host="redis.internal.example",
        port=6379,
        db=0,
        username="redis-user",
        password="redis-password",
        ssl=True,
        ssl_ca_certs=str(tmp_path / "redis-ca"),
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
    )
    client.ping.assert_called_once_with()
    client.close.assert_called_once_with()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_egress_mode_renders_egress_only_configuration(tmp_path):
    """Keep the Egress document free from LiveKit-only configuration."""
    redis_patch, _ = successful_redis()
    with redis_patch:
        output = render(egress_arguments(tmp_path))

    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["ws_url"] == "ws://livekit-signal.internal:7880"
    assert config["health_port"] == 9090
    assert config["api_key"] == "livekit-key"
    assert config["api_secret"] == "livekit-secret"
    assert config["cpu_cost"] == {"track_composite_cpu_cost": 1.0}
    assert config["session_limits"] == {
        "file_output_max_duration": "10m",
        "segment_output_max_duration": "10m",
    }
    assert "rtc" not in config
    assert "webhook" not in config
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        (["--node-ip", "203.0.113.9"], "node ip is absent or unbound"),
        (["--webhook-url", "http://meet-api.internal/"], "webhook url"),
    ],
)
def test_unbound_non_secret_parameters_are_refused(
    tmp_path, arguments, expected_message
):
    """Reject unbound non-secret values before writing an output file."""
    invocation = livekit_arguments(tmp_path)
    position = invocation.index(arguments[0]) + 1
    invocation[position] = "UNBOUND"

    with pytest.raises(ValueError, match=expected_message):
        render(invocation)
    assert not (tmp_path / "rendered.yaml").exists()


def test_unbound_mounted_secret_is_refused_before_redis_contact(tmp_path):
    """Reject an unbound mounted secret before contacting Redis."""
    invocation = egress_arguments(tmp_path)
    (tmp_path / "api-secret").write_text("UNBOUND\n", encoding="utf-8")

    with mock.patch("livekit_config_renderer.redis.Redis") as redis_constructor:
        with pytest.raises(ValueError, match="LiveKit API secret"):
            render(invocation)

    redis_constructor.assert_not_called()
    assert not (tmp_path / "rendered.yaml").exists()


def test_failed_redis_tls_check_does_not_replace_existing_output(tmp_path):
    """Preserve the previous output when Redis TLS verification fails."""
    output = tmp_path / "rendered.yaml"
    output.write_text("previous\n", encoding="utf-8")
    client = mock.Mock()
    client.ping.side_effect = RuntimeError("TLS verification failed")

    with mock.patch("livekit_config_renderer.redis.Redis", return_value=client):
        with pytest.raises(RuntimeError, match="TLS verification failed"):
            render(egress_arguments(tmp_path))

    assert output.read_text(encoding="utf-8") == "previous\n"
    client.close.assert_called_once_with()


def test_redis_server_name_must_match_the_verified_connection_host(tmp_path):
    """Reject TLS server names that differ from the Redis connection host."""
    invocation = egress_arguments(tmp_path)
    position = invocation.index("--redis-server-name") + 1
    invocation[position] = "different.internal.example"

    with mock.patch("livekit_config_renderer.redis.Redis") as redis_constructor:
        with pytest.raises(ValueError, match="must match"):
            render(invocation)

    redis_constructor.assert_not_called()
