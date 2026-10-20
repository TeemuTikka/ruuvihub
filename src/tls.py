"""Self-signed TLS certificate generation for RuuviHub's HTTPS listener.

RuuviHub is designed to run on a home/local network without a public
DNS name, so an ACME-issued certificate (Let's Encrypt et al.) isn't an
option out of the box. Instead, if HTTPS is enabled and no cert/key
files are configured (or the configured files don't exist yet), this
module generates a self-signed certificate the first time the app
starts, and reuses it on every subsequent start -- browsers will show a
one-time "not trusted" warning that must be clicked through (or the
cert imported), but every connection is still fully encrypted.

Users who want a browser-trusted cert (e.g. via ``mkcert``, an internal
CA, or a real domain + Let's Encrypt) can just point ``tls_cert_path``/
``tls_key_path`` at their own files -- this module never overwrites an
existing cert/key pair.
"""

import datetime
import ipaddress
import logging
import os
import socket
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

_CERT_VALID_DAYS = 3650  # ~10 years -- avoids nagging renewal for a self-signed home cert
_RSA_KEY_SIZE = 2048


def _local_alt_names() -> list:
    """Best-effort Subject Alternative Names covering common local access
    patterns: localhost, loopback IPs, and this machine's hostname/LAN IP.

    Best-effort because hostname/IP resolution can fail in constrained or
    offline environments (e.g. some CI/test sandboxes) -- those entries
    are simply skipped rather than failing cert generation entirely.
    """
    names: list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    try:
        hostname = socket.gethostname()
        if hostname:
            names.append(x509.DNSName(hostname))
    except OSError:
        pass
    try:
        # Doesn't actually send traffic; just asks the OS which local
        # interface would be used to reach an external address, which is
        # a reliable way to find the LAN IP without extra dependencies.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            lan_ip = probe.getsockname()[0]
        names.append(x509.IPAddress(ipaddress.ip_address(lan_ip)))
    except OSError:
        pass
    # De-duplicate while preserving order (a hostname could coincide with
    # "localhost", or LAN-IP lookup could return 127.0.0.1 in odd setups).
    seen = set()
    unique_names = []
    for name in names:
        key = (type(name), str(name.value))
        if key not in seen:
            seen.add(key)
            unique_names.append(name)
    return unique_names


def _write_private_key_secure(key_file: Path, data: bytes) -> None:
    """Write ``data`` to ``key_file`` with 0600 permissions from the very
    moment the file is created, then atomically move it into place.

    Writing directly to ``key_file`` and ``chmod()``-ing it afterwards
    leaves a window -- governed by the process umask, commonly 0644 --
    during which another local user can read the unencrypted private key.
    ``tempfile.mkstemp`` creates its file with mode 0600 already (no
    umask involved), so the key is never briefly exposed.
    """
    fd, tmp_path = tempfile.mkstemp(dir=str(key_file.parent), prefix=".ruuvihub-key-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, key_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def ensure_self_signed_cert(cert_path: str, key_path: str) -> None:
    """Generate a self-signed cert/key pair at the given paths if missing.

    No-op (and does not touch either file) if both already exist -- this
    is called on every startup, so an existing cert (self-signed or
    user-supplied) must never be silently regenerated/overwritten, which
    would invalidate a certificate a user or browser has already trusted.

    Raises ``FileExistsError`` if exactly one of the two files exists --
    a partial pair (e.g. a cert with no matching key, or vice versa) is
    always a misconfiguration, and silently "completing" it would
    overwrite a file the user may still need (e.g. a CA-issued
    certificate whose key path was simply misconfigured).
    """
    cert_file = Path(cert_path)
    key_file = Path(key_path)
    cert_exists = cert_file.exists()
    key_exists = key_file.exists()
    if cert_exists and key_exists:
        logger.info("Using existing TLS certificate: %s", cert_file)
        return
    if cert_exists != key_exists:
        existing = cert_file if cert_exists else key_file
        missing = key_file if cert_exists else cert_file
        raise FileExistsError(
            f"Found {existing} but its paired file {missing} is missing. "
            "Refusing to generate a new cert/key pair, which would "
            f"overwrite the existing {existing}. Restore the missing "
            "file, or remove the existing one to allow a fresh "
            "self-signed pair to be generated."
        )

    logger.info("No TLS certificate found at %s – generating a self-signed one", cert_file)
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=_RSA_KEY_SIZE)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "RuuviHub (self-signed)")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=_CERT_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(_local_alt_names()), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    _write_private_key_secure(
        key_file,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    logger.info(
        "Generated self-signed TLS certificate (valid %d days): %s",
        _CERT_VALID_DAYS,
        cert_file,
    )
