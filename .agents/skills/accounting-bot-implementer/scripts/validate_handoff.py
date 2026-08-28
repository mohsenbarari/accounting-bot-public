from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_FILES = {
    "handoff.md": (
        "## Identity",
        "## Scope",
        "## Roadmap traceability",
        "## Changed files",
        "## Schema and migrations",
        "## Commands and exit codes",
        "## Tests and evidence",
        "## Assumptions and open items",
        "## Risks",
        "## Rollback",
        "## Protected assets",
        "## Stop state",
    ),
    "acceptance-matrix.md": (
        "# Acceptance Matrix",
        "## Coverage gaps",
        "## Gate statement",
    ),
    "test-results.txt": (
        "WORK PACKAGE:",
        "PHASE:",
        "RECORDED AT UTC:",
        "FINAL SUMMARY",
    ),
}

PLACEHOLDER_PATTERNS = (
    re.compile(r"<[^>\r\n]+>"),
    re.compile(r"\b(?:TODO|TBD|FILL[ _-]?ME)\b", re.IGNORECASE),
)


def validate_directory(directory: Path, *, allow_placeholders: bool) -> list[str]:
    errors: list[str] = []

    if not directory.is_dir():
        return [f"Directory not found: {directory}"]

    for filename, required_markers in REQUIRED_FILES.items():
        path = directory / filename
        if not path.is_file():
            errors.append(f"Missing required file: {path}")
            continue

        content = path.read_text(encoding="utf-8")
        if not content.strip():
            errors.append(f"File is empty: {path}")
            continue

        for marker in required_markers:
            if marker not in content:
                errors.append(f"Missing marker {marker!r} in {path}")

        if not allow_placeholders:
            for pattern in PLACEHOLDER_PATTERNS:
                match = pattern.search(content)
                if match:
                    errors.append(
                        f"Unresolved placeholder {match.group(0)!r} in {path}"
                    )
                    break

    return errors


def validate_templates(skill_root: Path) -> list[str]:
    assets = skill_root / "assets"
    mapping = {
        "handoff-template.md": REQUIRED_FILES["handoff.md"],
        "acceptance-matrix-template.md": REQUIRED_FILES["acceptance-matrix.md"],
        "test-results-template.txt": REQUIRED_FILES["test-results.txt"],
    }
    errors: list[str] = []

    for filename, required_markers in mapping.items():
        path = assets / filename
        if not path.is_file():
            errors.append(f"Missing template: {path}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in required_markers:
            if marker not in content:
                errors.append(f"Missing marker {marker!r} in {path}")

    adr = assets / "adr-template.md"
    if not adr.is_file():
        errors.append(f"Missing template: {adr}")
    elif "## Options considered" not in adr.read_text(encoding="utf-8"):
        errors.append(f"Missing ADR options section in {adr}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an accounting-bot implementation handoff."
    )
    parser.add_argument("handoff_directory", nargs="?", type=Path)
    parser.add_argument(
        "--check-templates",
        action="store_true",
        help="Validate templates shipped beside this script.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.check_templates:
        skill_root = Path(__file__).resolve().parent.parent
        errors = validate_templates(skill_root)
    elif args.handoff_directory is not None:
        errors = validate_directory(
            args.handoff_directory.resolve(), allow_placeholders=False
        )
    else:
        print("Provide a handoff directory or use --check-templates.", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Handoff validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
