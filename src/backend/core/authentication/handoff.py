"""Session rehydration backend for proven Mastrao host identities only."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist

from core.mastrao_identity import is_mastrao_host_subject

UserModel = get_user_model()


class MastraoHostAuthenticationBackend:
    """Reload only identities created by the signed host handoff consumer."""

    def authenticate(self, request, **credentials):  # pylint: disable=unused-argument
        """Never authenticate credentials directly; the handoff view calls login."""
        return None

    def get_user(self, user_id):
        """Rehydrate only a host identity established by the handoff protocol."""
        try:
            user = UserModel.objects.select_related("mastrao_host_identity").get(
                pk=user_id,
                is_device=False,
                is_active=True,
            )
        except UserModel.DoesNotExist:
            return None
        if not is_mastrao_host_subject(user.sub):
            return None
        try:
            identity = user.mastrao_host_identity
        except ObjectDoesNotExist:
            return None
        if identity.user_id != user.id:
            return None
        return user
