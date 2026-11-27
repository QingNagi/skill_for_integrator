#!/usr/bin/env python3
"""Audit pinned release wheels, optionally checking PyPI advisories or extracting them for scanners."""
import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
import fetch_vendor as fv


def audit(wheels, extract_to=None, check_pypi=False):
    releases = {}
    findings = []
    for path, spec in wheels:
        name, size, digest, _ = spec
        if not fv.is_good(path, size, digest):
            findings.append("Missing or mismatched wheel: " + name)
            continue
        print("SHA256 OK", name, digest)
        if extract_to:
            # Only original files matching the pins are eligible for extraction.
            with zipfile.ZipFile(path) as wheel:
                wheel.extractall(extract_to / path.stem)
        if check_pypi:
            package, version = name.split("-")[:2]
            key = package, version
            if key not in releases:
                url = "https://pypi.org/pypi/%s/%s/json" % key
                with fv.mirror_opener(url).open(url, timeout=fv.REQUEST_TIMEOUT_SECONDS) as response:
                    releases[key] = json.load(response)
                advisories = [row["id"] for row in releases[key]["vulnerabilities"]
                              if not row.get("withdrawn")]
                print("PyPI", package, version, len(advisories), "advisory records")
                if advisories:
                    findings.append("%s %s: %s" % (package, version, ", ".join(advisories)))
            urls = releases[key]["urls"]
            if not any(item["filename"] == name and item["size"] == size
                       and item["digests"]["sha256"] == digest for item in urls):
                findings.append("Pin does not match PyPI: " + name)
    for message in findings:
        print(message, file=sys.stderr)
    return not findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-dir", type=Path, default=Path(fv.VENDOR), help="Wheel input directory (default: .build/vendor)")
    parser.add_argument("--platform", choices=["lite", "all"] + sorted(fv.WHEELS), default="lite")
    parser.add_argument("--check-pypi", action="store_true", help="Compare with current public release metadata")
    parser.add_argument("--extract-to", type=Path, help="Extract into a new directory for independent scans")
    args = parser.parse_args(argv)
    root = args.vendor_dir
    wheels = [(root / folder / spec[0], spec) for folder, spec in fv.wheel_specs(args.platform)]
    try:
        if args.extract_to:
            args.extract_to.mkdir(parents=True, exist_ok=False)
        return 0 if audit(wheels, args.extract_to, args.check_pypi) else 1
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("Audit incomplete:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
