#!/usr/bin/env python3
"""Validate a release ref and derive its safe OCI tag representation."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

NUMERIC = r"(?:0|[1-9][0-9]*)"
NON_NUMERIC = r"(?:[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
IDENTIFIER = rf"(?:{NUMERIC}|{NON_NUMERIC})"
PRERELEASE = rf"(?:{IDENTIFIER}(?:\.{IDENTIFIER})*)"
BUILD = r"(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)"
TAG = re.compile(
    rf"^v(?P<core>{NUMERIC}\.{NUMERIC}\.{NUMERIC})"
    rf"(?P<prerelease>-(?P<pre>{PRERELEASE}))?"
    rf"(?P<build>\+(?P<meta>{BUILD}))?$"
)


def classify(tag: str) -> tuple[str, str, bool]:
    """Return SemVer text, Docker-safe tag text, and stable-release status."""
    match = TAG.fullmatch(tag)
    if match is None:
        raise ValueError(
            "expected vMAJOR.MINOR.PATCH with optional SemVer prerelease/build metadata"
        )
    version = tag.removeprefix("v")
    # OCI/Docker tags reject '+'. Preserve build metadata without conflating it
    # with another release by using Docker's allowed underscore character.
    image_tag = version.replace("+", "_")
    if len(image_tag) > 128:
        raise ValueError("derived OCI image tag exceeds Docker's 128-character limit")
    return version, image_tag, match.group("pre") is None


def self_test() -> None:
    accepted = {
        "v0.1.0": ("0.1.0", "0.1.0", True),
        "v1.2.3-alpha.1+stable": ("1.2.3-alpha.1+stable", "1.2.3-alpha.1_stable", False),
        "v1.2.3+build.7": ("1.2.3+build.7", "1.2.3_build.7", True),
    }
    for tag, expected in accepted.items():
        assert classify(tag) == expected, tag
    assert classify("v1.2.3+" + "a" * 122)[1] == "1.2.3_" + "a" * 122
    for tag in ("v1.2.3-", "v01.2.3", "v1.2.3-01", "v1.2", "1.2.3"):
        try:
            classify(tag)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid tag: {tag}")
    try:
        classify("v1.2.3+" + "a" * 123)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a Docker image tag longer than 128 characters")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", nargs="?")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.tag is None:
        parser.error("tag is required unless --self-test is used")
    version, image_tag, stable = classify(args.tag)
    output = f"version={version}\nimage_tag={image_tag}\nstable={str(stable).lower()}\n"
    if args.github_output:
        args.github_output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
