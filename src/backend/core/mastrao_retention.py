"""Explicit content retention; authorization and worker leases stay bounded."""

from datetime import UTC, datetime

from django.db.models import Q

from core.mastrao_recording_contract import RecordingContractRefused

BOUNDED = "bounded"
UNTIL_DELETED = "until_deleted"


def retention_datetime(payload):
    """Project validated wire retention into a nullable database timestamp."""
    expiry = payload["retention_expires_at"]
    if expiry is None:
        return None
    return datetime.fromtimestamp(expiry, tz=UTC)


def retention_claims(binding):
    """Serialize known stored retention, retaining the exact historical shape."""
    mode = binding.retention_mode
    expiry = binding.retention_expires_at
    if mode == UNTIL_DELETED and expiry is None:
        return {"retention_mode": UNTIL_DELETED, "retention_expires_at": None}
    if mode == BOUNDED and expiry is not None:
        return {"retention_expires_at": int(expiry.timestamp())}
    raise RecordingContractRefused()


def retention_matches(binding, payload):
    """An old unknown deadline can never authorize persistent content."""
    try:
        stored = retention_claims(binding)
    except RecordingContractRefused:
        return False
    return stored == {
        key: payload[key]
        for key in ("retention_mode", "retention_expires_at")
        if key in payload
    }


def active_retention(now, *, prefix=""):
    """Filter content only; callers retain their own identity and lease checks."""
    return Q(
        **{
            f"{prefix}retention_mode": UNTIL_DELETED,
            f"{prefix}retention_expires_at__isnull": True,
        }
    ) | Q(
        **{
            f"{prefix}retention_mode": BOUNDED,
            f"{prefix}retention_expires_at__gt": now,
        }
    )
