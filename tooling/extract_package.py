"""Dry-run helper for extracting a package to its own repository (ADR-0010).

    uv run python tooling/extract_package.py taskport-jobs --dry-run

This performs NO git operations and publishes nothing (section 61). It verifies
the package is self-contained and prints the exact commands a maintainer would
run. See docs/family/extraction.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = ("pyproject.toml", "README.md", "CHANGELOG.md", "LICENSE")


def check_self_contained(package_dir: Path) -> list[str]:
    problems: list[str] = []
    for name in REQUIRED_FILES:
        if not (package_dir / name).exists():
            problems.append(f"missing {name}")
    if not (package_dir / "src").is_dir():
        problems.append("missing src/ directory")
    if not (package_dir / "tests").is_dir():
        problems.append("missing tests/ directory")
    # A taskport/__init__.py would shadow the namespace (ADR-0003).
    if (package_dir / "src" / "taskport" / "__init__.py").exists():
        problems.append("src/taskport/__init__.py present — breaks the namespace")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="distribution name, e.g. taskport-jobs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="only preview (the sole supported mode in this phase)",
    )
    args = parser.parse_args()

    package_dir = ROOT / "packages" / args.package
    if not package_dir.exists():
        print(f"error: unknown package {args.package!r}", file=sys.stderr)
        return 2

    print(f"Extraction preview for {args.package} (DRY RUN — nothing will change)\n")

    problems = check_self_contained(package_dir)
    if problems:
        print("Self-containment issues:")
        for problem in problems:
            print("  -", problem)
        print()
    else:
        print(
            "Self-contained: OK (has its own pyproject/README/CHANGELOG/LICENSE/"
            "src/tests, no namespace shadow).\n"
        )

    print("Files that would move to the new repo root:")
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            print("  ", path.relative_to(package_dir))

    print("\nManual commands a maintainer would run (NOT executed here):\n")
    print(f"  git clone <monorepo> {args.package} && cd {args.package}")
    print(f"  git filter-repo --subdirectory-filter packages/{args.package}")
    print("  # remove this package's [tool.uv.sources] workspace pin (deps resolve from PyPI)")
    print("  cp tooling/ci-templates/package-ci.yml .github/workflows/ci.yml")
    print("  cp tooling/ci-templates/release.yml   .github/workflows/release.yml")
    print("  # configure PyPI Trusted Publishing, then create a GitHub Release")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
