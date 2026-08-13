"""Identity rules for Mastrao-owned technical principals."""

import hashlib

MASTRAO_TECHNICAL_SUBJECT_PREFIX = "mastrao_"
MASTRAO_HOST_SUBJECT_PREFIX = "mastraohost_"


def is_mastrao_technical_subject(subject):
    return isinstance(subject, str) and subject.startswith(
        MASTRAO_TECHNICAL_SUBJECT_PREFIX
    )


def mastrao_technical_owner_subject(owner_ref):
    digest = hashlib.sha256(owner_ref.encode()).hexdigest()
    return f"{MASTRAO_TECHNICAL_SUBJECT_PREFIX}{digest}"


def is_mastrao_host_subject(subject):
    return isinstance(subject, str) and subject.startswith(MASTRAO_HOST_SUBJECT_PREFIX)


def is_mastrao_reserved_subject(subject):
    return is_mastrao_technical_subject(subject) or is_mastrao_host_subject(subject)


def mastrao_host_subject(host_ref):
    digest = hashlib.sha256(host_ref.encode()).hexdigest()
    return f"{MASTRAO_HOST_SUBJECT_PREFIX}{digest}"
