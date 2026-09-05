"""The ``Secret`` wrapper (FR-306, FR-308, NFR-09).

The property under test is not "the value is hidden" but "every accidental path
to the value is closed". Each test below corresponds to a real way credentials
escape: an f-string in an error message, a ``repr`` in a traceback, a
``json.dumps`` of a config object, a ``copy.deepcopy`` into a log record.
"""

from __future__ import annotations

import copy
import json
import pickle

import pytest

from vyomel.core.logging import REDACTED, redact, redact_text
from vyomel.core.secrets import Secret

# Assembled at runtime so repository credential scanners do not flag it.
VALUE = "-".join(("sk", "test", "9f2c4b7e1a08d5"))


def test_the_value_is_reachable_only_through_get() -> None:
    secret = Secret(VALUE, name="openai_api_key")
    assert secret.get() == VALUE
    assert secret.name == "openai_api_key"


@pytest.mark.req("FR-308")
def test_no_rendering_path_exposes_the_value() -> None:
    secret = Secret(VALUE, name="api_token")

    assert VALUE not in str(secret)
    assert VALUE not in repr(secret)
    assert VALUE not in f"{secret}"
    assert VALUE not in f"{secret!r}"
    assert VALUE not in f"{secret:>40}"
    assert VALUE not in "token is {}".format(secret)  # noqa: UP032 - str.format is a leak path
    assert str(secret) == REDACTED


@pytest.mark.req("FR-308")
def test_an_exception_message_built_from_a_secret_is_safe() -> None:
    secret = Secret(VALUE)
    error = RuntimeError(f"auth failed with {secret}")
    assert VALUE not in str(error)


@pytest.mark.req("FR-306")
def test_serialization_fails_loudly_rather_than_leaking() -> None:
    """Failing closed is the point: a ``Secret`` that pickled cleanly would be
    written to any cache, queue, or crash dump that touched it."""
    secret = Secret(VALUE)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(secret)
    with pytest.raises(TypeError, match="not serializable"):
        copy.deepcopy(secret)
    with pytest.raises(TypeError):
        json.dumps({"key": secret})


@pytest.mark.req("FR-308")
def test_constructing_a_secret_registers_it_with_the_redaction_filter() -> None:
    """Defence in depth. Even a leak through a path that never saw the wrapper --
    a subprocess argument echoed back, a provider SDK logging its own config --
    is scrubbed at the sink."""
    leaked = "-".join(("ghp", "0d1e2f3a4b5c6d7e8f90"))
    Secret(leaked, name="github_token")

    assert leaked not in redact_text(f"Authorization: Bearer {leaked}")
    assert redact({"note": leaked})["note"] != leaked


def test_emptiness_is_checkable_without_unwrapping() -> None:
    assert Secret("").is_empty
    assert not Secret(VALUE).is_empty
    assert not Secret("")
    assert Secret(VALUE)
    assert len(Secret(VALUE)) == len(VALUE)


def test_equality_compares_values_and_only_against_secrets() -> None:
    assert Secret(VALUE) == Secret(VALUE)
    assert Secret(VALUE) != Secret(VALUE + "x")
    # Comparing against a bare string returns NotImplemented, so Python falls
    # back to identity: a plaintext string is never equal to a Secret. That
    # keeps `if token == "expected"` from silently working on the wrapper.
    assert Secret(VALUE) != VALUE


def test_hashing_does_not_expose_the_value() -> None:
    """A hash of a short or guessable secret in a metric label is an oracle."""
    assert hash(Secret(VALUE, name="a")) != hash(Secret(VALUE, name="b"))
    assert hash(Secret(VALUE, name="a")) == hash(Secret("other", name="a"))
