#!/usr/bin/env python3
"""Choose the next stable patch tag, with a floor for the first public release."""

import argparse
import re
import subprocess

_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def next_release_tag(tags: list[str], minimum_version: str = "v1.1.0") -> str:
    minimum = _VERSION.fullmatch(minimum_version)
    if minimum is None:
        raise ValueError("minimum_version must be a stable vX.Y.Z tag")
    floor = tuple(map(int, minimum.groups()))
    versions = [tuple(map(int, match.groups())) for tag in tags
                if (match := _VERSION.fullmatch(tag))]
    if versions:
        major, minor, patch = max(versions)
        version = max(floor, (major, minor, patch + 1))
    else:
        version = floor
    return "v" + ".".join(map(str, version))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-version", default="v1.1.0")
    args = parser.parse_args()
    tags = subprocess.check_output(["git", "tag", "--list"], text=True).splitlines()
    print(next_release_tag(tags, args.minimum_version))
