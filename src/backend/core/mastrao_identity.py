"""Identity rules for Mastrao-owned technical principals."""

import hashlib

MASTRAO_TECHNICAL_SUBJECT_PREFIX = "mastrao_"


def is_mastrao_technical_subject(subject):
    return isinstance(subject, str) and subject.startswith(
        MASTRAO_TECHNICAL_SUBJECT_PREFIX
    )


def mastrao_technical_owner_subject(owner_ref):
    digest = hashlib.sha256(owner_ref.encode()).hexdigest()
    return f"{MASTRAO_TECHNICAL_SUBJECT_PREFIX}{digest}"
