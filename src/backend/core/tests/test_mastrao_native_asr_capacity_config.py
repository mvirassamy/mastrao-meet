"""Native ASR dispatch capacity must leave a fresh slot at both scopes."""

from django.core.exceptions import ImproperlyConfigured

import pytest

from core.mastrao_native_asr_worker import _capacity_limits


def test_staging_capacity_defaults(settings):
    assert _capacity_limits() == (4, 4, 4)


@pytest.mark.parametrize(
    ("global_limit", "tenant_limit", "meeting_limit"),
    [(1, 1, 1), (4, 1, 1), (4, 5, 4), (4, 3, 4)],
)
def test_incoherent_capacity_is_rejected(
    settings, global_limit, tenant_limit, meeting_limit
):
    settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT = global_limit
    settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT = tenant_limit
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = meeting_limit
    with pytest.raises(ImproperlyConfigured):
        _capacity_limits()
