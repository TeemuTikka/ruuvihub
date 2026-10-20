"""Tests for src/tls.py – self-signed certificate generation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cryptography import x509

from tls import ensure_self_signed_cert


def test_generates_cert_and_key_when_missing(tmp_path):
    cert_path = tmp_path / "ruuvihub.crt"
    key_path = tmp_path / "ruuvihub.key"

    ensure_self_signed_cert(str(cert_path), str(key_path))

    assert cert_path.exists()
    assert key_path.exists()


def test_generated_cert_has_expected_subject_and_san(tmp_path):
    cert_path = tmp_path / "ruuvihub.crt"
    key_path = tmp_path / "ruuvihub.key"

    ensure_self_signed_cert(str(cert_path), str(key_path))

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns_names = san.get_values_for_type(x509.DNSName)

    assert "localhost" in dns_names
    assert cert.not_valid_after_utc > cert.not_valid_before_utc


def test_does_not_overwrite_existing_cert(tmp_path):
    """An existing cert/key pair (self-signed or user-supplied) must never
    be silently regenerated -- that would invalidate a cert a browser or
    operator has already trusted."""
    cert_path = tmp_path / "ruuvihub.crt"
    key_path = tmp_path / "ruuvihub.key"

    ensure_self_signed_cert(str(cert_path), str(key_path))
    first_cert_bytes = cert_path.read_bytes()
    first_key_bytes = key_path.read_bytes()

    ensure_self_signed_cert(str(cert_path), str(key_path))

    assert cert_path.read_bytes() == first_cert_bytes
    assert key_path.read_bytes() == first_key_bytes


def test_raises_on_partial_pair_instead_of_overwriting(tmp_path):
    """If only one of cert/key exists (e.g. an operator's key file went
    missing), generating a fresh pair would silently destroy the
    existing file -- this must be treated as an error instead."""
    import pytest

    cert_path = tmp_path / "ruuvihub.crt"
    key_path = tmp_path / "ruuvihub.key"
    cert_path.write_bytes(b"existing-ca-issued-cert")

    with pytest.raises(FileExistsError):
        ensure_self_signed_cert(str(cert_path), str(key_path))

    # The existing file must be untouched, and no key generated alongside it.
    assert cert_path.read_bytes() == b"existing-ca-issued-cert"
    assert not key_path.exists()


def test_creates_parent_directories(tmp_path):
    cert_path = tmp_path / "nested" / "dir" / "ruuvihub.crt"
    key_path = tmp_path / "nested" / "dir" / "ruuvihub.key"

    ensure_self_signed_cert(str(cert_path), str(key_path))

    assert cert_path.exists()
    assert key_path.exists()


def test_key_file_is_not_world_readable(tmp_path):
    cert_path = tmp_path / "ruuvihub.crt"
    key_path = tmp_path / "ruuvihub.key"

    ensure_self_signed_cert(str(cert_path), str(key_path))

    if os.name != "nt":  # POSIX file mode bits aren't meaningful on Windows
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600
