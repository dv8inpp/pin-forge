"""
Minimal auth for a personal, single-admin tool -- no user accounts, no
registration, just one shared password gating the whole app. That's the
right amount of security for "one person, running this for themselves,"
not a multi-tenant product.

The password lives in .env as plain text (ADMIN_PASSWORD), same as your
other secrets there (Pinterest app secret, Amazon credentials). It's
compared with a timing-safe function so response time can't be used to
guess it character by character.
"""
import os
import secrets
from flask import session


def check_password(candidate: str) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not expected:
        # Fail closed: if no password is configured, nobody gets in rather
        # than everybody getting in. Set ADMIN_PASSWORD in .env to unlock.
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def is_logged_in() -> bool:
    return session.get("logged_in") is True


def log_in():
    session.permanent = True
    session["logged_in"] = True


def log_out():
    session.clear()
