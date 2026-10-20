#!/usr/bin/env python3
"""Generate a self-signed TLS cert/key pair if missing (idempotent).

Invoked by install.sh/update.sh through the release's own virtualenv,
running as the service account rather than root (via lib.sh's
``as_service_user``) -- release code (``tls.py``) must never be
imported/executed as root, since a compromised service account could
otherwise modify that file between chown and invocation and gain root
code execution. The release's ``src/`` directory, cert path, and key
path are all passed as argv (never interpolated into the script text
itself), so an untrusted path/value can't be used to inject code.
"""

import sys


def main() -> None:
    src_dir, cert_path, key_path = sys.argv[1:4]
    sys.path.insert(0, src_dir)
    from tls import ensure_self_signed_cert

    ensure_self_signed_cert(cert_path, key_path)


if __name__ == "__main__":
    main()
