#!/usr/bin/env python3
"""Idempotently persist shared/certs paths into settings.local.json.

Used by update.sh to migrate installations created before HTTPS was
added to RuuviHub: without this, an existing settings.local.json has
empty ``tls_cert_path``/``tls_key_path``, so ``run_web_server`` falls
back to its per-release default (a path inside that release's own
directory) and generates a *new* self-signed cert on every update,
invalidating the previously-trusted certificate each time.

Only fills a path in if it is still empty, so a value an operator has
already customized (e.g. pointing at an mkcert-issued certificate) is
never overwritten.
"""

import json
import sys


def main() -> None:
    settings_path, cert_path, key_path = sys.argv[1:4]
    with open(settings_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False
    if not data.get("tls_cert_path"):
        data["tls_cert_path"] = cert_path
        changed = True
    if not data.get("tls_key_path"):
        data["tls_key_path"] = key_path
        changed = True

    if changed:
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
