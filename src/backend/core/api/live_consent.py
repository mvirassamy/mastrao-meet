"""Session-bound browser routes for Core-owned live transcription choices."""

# RoomViewSet supplies get_object; the mixin keeps this feature out of its media paths.
# pylint: disable=abstract-method,no-member,too-few-public-methods

from django.conf import settings
from django.http import Http404

from rest_framework import decorators, serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from core.mastrao_guest_contract import GuestHandoffRefused
from core.mastrao_guest_grant import (
    active_guest_lifecycle_grant,
    can_access_canonical_room,
)
from core.mastrao_host_contract import HostHandoffRefused
from core.mastrao_host_grant import active_host_close_grant
from core.mastrao_live_consent import offer_live_consent, record_live_consent
from core.mastrao_recording_contract import RecordingContractRefused


class LiveConsentDecisionSerializer(serializers.Serializer):
    """Accept only the displayed snapshot and one stable browser choice ID."""

    decision_ref = serializers.RegexField(
        r"^[A-Za-z0-9_-]{16,160}$", trim_whitespace=False
    )
    decision = serializers.ChoiceField(choices=("accepted", "refused", "withdrawn"))
    consent_ref = serializers.RegexField(
        r"^[A-Za-z0-9_-]{16,160}$", trim_whitespace=False
    )
    policy_ref = serializers.RegexField(
        r"^[A-Za-z0-9_-]{16,160}$", trim_whitespace=False
    )
    notice_version = serializers.RegexField(
        r"^[A-Za-z0-9_-]{16,160}$", trim_whitespace=False
    )
    notice_digest = serializers.RegexField(r"^[a-f0-9]{64}$", trim_whitespace=False)
    retention_expires_at = serializers.IntegerField(min_value=1, max_value=2**53 - 1)

    def validate(self, attrs):
        """Do not discard browser-supplied authority or coerce its snapshot."""
        if set(self.initial_data) != set(self.fields):
            raise ValidationError("Unexpected consent fields.")
        if type(self.initial_data["retention_expires_at"]) is not int:  # pylint: disable=unidiomatic-typecheck
            raise ValidationError("Invalid retention timestamp.")
        return attrs


class LiveConsentActions:
    """Record a choice without creating a media token, dispatch or ASR attempt."""

    def _live_consent_room(self, request):
        # Guest grants live in an anonymous Django session. DRF's default session
        # authenticator alone does not enforce CSRF for that browser identity.
        SessionAuthentication().enforce_csrf(request)
        if not settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED:
            raise Http404
        room = self.get_object()
        if not hasattr(room, "mastrao_binding"):
            raise Http404
        withdrawal = (
            self.action == "live_consent_decision"
            and isinstance(request.data, dict)
            and request.data.get("decision") == "withdrawn"
        )
        if withdrawal:
            if not (
                active_host_close_grant(request, room)
                or active_guest_lifecycle_grant(request, room)
            ):
                raise Http404
        elif not can_access_canonical_room(request, room):
            raise Http404
        return room

    @decorators.action(
        detail=True,
        methods=["post"],
        url_path="live-consent-offer",
        permission_classes=[],
        parser_classes=[JSONParser],
    )
    def live_consent_offer(self, request, pk=None):  # pylint: disable=unused-argument
        """Obtain the exact Core notice for this browser's host or guest grant."""
        room = self._live_consent_room(request)
        if request.data != {}:
            raise ValidationError("The consent offer takes no browser authority.")
        try:
            return Response(offer_live_consent(request, room))
        except (
            RecordingContractRefused,
            HostHandoffRefused,
            GuestHandoffRefused,
        ) as error:
            return _refusal(error)

    @decorators.action(
        detail=True,
        methods=["post"],
        url_path="live-consent-decision",
        permission_classes=[],
        parser_classes=[JSONParser],
    )
    def live_consent_decision(self, request, pk=None):  # pylint: disable=unused-argument
        """Sign the displayed snapshot and decision for Core to verify and persist."""
        room = self._live_consent_room(request)
        serializer = LiveConsentDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(
                record_live_consent(request, room, **serializer.validated_data)
            )
        except (
            RecordingContractRefused,
            HostHandoffRefused,
            GuestHandoffRefused,
        ) as error:
            return _refusal(error)


def _refusal(error):
    message = "Unavailable"
    if error.status == 404:
        message = "Not found"
    elif error.status == 409:
        message = "Consent conflict"
    return Response({"message": message}, status=error.status)
