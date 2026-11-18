"""Render LiveKit and Egress configuration from mounted secret files."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

import redis


def _bound_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.upper() == "UNBOUND":
        raise ValueError(f"{label} is absent or unbound")
    return normalized


def _secret(path: Path, label: str) -> str:
    try:
        return _bound_text(path.read_text(encoding="utf-8"), label)
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _nonnegative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive number")
    return parsed


def _verify_redis_tls(args: argparse.Namespace, username: str, password: str) -> None:
    if args.redis_server_name != args.redis_host:
        raise ValueError("Redis TLS server name must match the connection host")
    client = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        username=username,
        password=password,
        ssl=True,
        ssl_ca_certs=str(args.redis_ca_file),
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        socket_connect_timeout=args.redis_timeout_seconds,
        socket_timeout=args.redis_timeout_seconds,
    )
    try:
        if client.ping() is not True:
            raise ValueError("Redis TLS ping was refused")
    finally:
        client.close()


def _redis_config(
    args: argparse.Namespace, username: str, password: str
) -> dict[str, Any]:
    return {
        "address": f"{args.redis_host}:{args.redis_port}",
        "db": args.redis_db,
        "username": username,
        "password": password,
        "tls": {
            "enabled": True,
            "insecure": False,
            "server_name": args.redis_server_name,
            "ca_cert_file": str(args.redis_ca_file),
        },
    }


def _livekit_config(
    args: argparse.Namespace,
    api_key: str,
    api_secret: str,
    redis_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "port": args.port,
        "bind_addresses": args.bind_address,
        "development": args.development,
        "rtc": {
            "tcp_port": args.rtc_tcp_port,
            "force_tcp": args.force_tcp,
            "udp_port": args.rtc_udp_port,
            "ips": {"includes": args.rtc_ip_include},
            "use_external_ip": args.use_external_ip,
            "advertise_internal_ip": args.advertise_internal_ip,
            "enable_loopback_candidate": args.enable_loopback_candidate,
            "node_ip": args.node_ip,
            "stun_servers": args.stun_server,
        },
        "redis": redis_config,
        "keys": {api_key: api_secret},
        "room": {"auto_create": args.auto_create_rooms},
        "logging": {"level": args.log_level, "json": args.json_logging},
        "webhook": {"api_key": api_key, "urls": [args.webhook_url]},
    }


def _egress_config(
    args: argparse.Namespace,
    api_key: str,
    api_secret: str,
    redis_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "ws_url": args.ws_url,
        "redis": redis_config,
        "health_port": args.health_port,
        "logging": {"level": args.log_level, "json": args.json_logging},
        "disallow_local_storage": args.disallow_local_storage,
        "enable_chrome_sandbox": args.enable_chrome_sandbox,
        "cpu_cost": {
            "track_composite_cpu_cost": args.track_composite_cpu_cost,
        },
        "session_limits": {
            "file_output_max_duration": args.file_output_max_duration,
            "segment_output_max_duration": args.segment_output_max_duration,
        },
    }


def _write_atomically(output: Path, config: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            json.dump(config, temporary, separators=(",", ":"), sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        os.chmod(output, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--api-secret-file", required=True, type=Path)
    parser.add_argument("--redis-username-file", required=True, type=Path)
    parser.add_argument("--redis-password-file", required=True, type=Path)
    parser.add_argument("--redis-host", required=True)
    parser.add_argument("--redis-port", required=True, type=_positive_integer)
    parser.add_argument("--redis-db", required=True, type=_nonnegative_integer)
    parser.add_argument("--redis-ca-file", required=True, type=Path)
    parser.add_argument("--redis-server-name", required=True)
    parser.add_argument("--redis-timeout-seconds", required=True, type=_positive_float)
    parser.add_argument("--log-level", required=True)
    parser.add_argument("--json-logging", required=True, type=_boolean)
    parser.add_argument("--output", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a fail-closed LiveKit or Egress configuration"
    )
    modes = parser.add_subparsers(dest="mode", required=True)

    livekit = modes.add_parser("livekit")
    _add_common_arguments(livekit)
    livekit.add_argument("--port", required=True, type=_positive_integer)
    livekit.add_argument("--bind-address", required=True, action="append")
    livekit.add_argument("--development", required=True, type=_boolean)
    livekit.add_argument("--rtc-tcp-port", required=True, type=_positive_integer)
    livekit.add_argument("--rtc-udp-port", required=True, type=_positive_integer)
    livekit.add_argument("--rtc-ip-include", required=True, action="append")
    livekit.add_argument("--force-tcp", required=True, type=_boolean)
    livekit.add_argument("--use-external-ip", required=True, type=_boolean)
    livekit.add_argument("--advertise-internal-ip", required=True, type=_boolean)
    livekit.add_argument("--enable-loopback-candidate", required=True, type=_boolean)
    livekit.add_argument("--node-ip", required=True)
    stun_servers = livekit.add_mutually_exclusive_group(required=True)
    stun_servers.add_argument("--stun-server", action="append")
    stun_servers.add_argument("--no-stun-servers", action="store_true")
    livekit.add_argument("--auto-create-rooms", required=True, type=_boolean)
    livekit.add_argument("--webhook-url", required=True)

    egress = modes.add_parser("egress")
    _add_common_arguments(egress)
    egress.add_argument("--ws-url", required=True)
    egress.add_argument("--health-port", required=True, type=_positive_integer)
    egress.add_argument("--disallow-local-storage", required=True, type=_boolean)
    egress.add_argument("--enable-chrome-sandbox", required=True, type=_boolean)
    egress.add_argument(
        "--track-composite-cpu-cost", required=True, type=_positive_float
    )
    egress.add_argument("--file-output-max-duration", required=True)
    egress.add_argument("--segment-output-max-duration", required=True)
    return parser


def render(arguments: Sequence[str]) -> Path:
    args = _parser().parse_args(arguments)
    for name, value in vars(args).items():
        if isinstance(value, str):
            setattr(args, name, _bound_text(value, name.replace("_", " ")))
        elif isinstance(value, list):
            setattr(
                args,
                name,
                [_bound_text(item, name.replace("_", " ")) for item in value],
            )
        elif isinstance(value, Path):
            _bound_text(str(value), name.replace("_", " "))

    if args.mode == "livekit" and args.no_stun_servers:
        args.stun_server = []

    api_key = _secret(args.api_key_file, "LiveKit API key")
    api_secret = _secret(args.api_secret_file, "LiveKit API secret")
    redis_username = _secret(args.redis_username_file, "Redis username")
    redis_password = _secret(args.redis_password_file, "Redis password")
    if not args.redis_ca_file.is_file():
        raise ValueError("Redis CA file cannot be read")

    _verify_redis_tls(args, redis_username, redis_password)
    redis_config = _redis_config(args, redis_username, redis_password)
    if args.mode == "livekit":
        config = _livekit_config(args, api_key, api_secret, redis_config)
    else:
        config = _egress_config(args, api_key, api_secret, redis_config)
    _write_atomically(args.output, config)
    return args.output


def main() -> None:
    render(os.sys.argv[1:])


if __name__ == "__main__":
    main()
