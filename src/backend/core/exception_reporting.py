"""Redact credential-bearing application settings from Django error reports."""

from django.views.debug import SafeExceptionReporterFilter


class MeetExceptionReporterFilter(SafeExceptionReporterFilter):
    """Keep Django's filtering and hide the dedicated subtitle lock route."""

    def cleanse_setting(self, key, value):
        """Mask the lock DSN, whose setting name lacks a password marker."""

        if key == "ROOM_SUBTITLE_LOCK_DATABASE_URL":
            return self.cleansed_substitute
        return super().cleanse_setting(key, value)
