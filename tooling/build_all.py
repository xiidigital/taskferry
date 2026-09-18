"""Build wheels + sdists for every Taskferry package into ``dist/``.

    uv run python tooling/build_all.py

Each package is built independently (as it would be in its own repo), proving the
distributions are separable (ADR-0014). Nothing is uploaded.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = ROOT / "packages"
DIST = ROOT / "dist"


def main() -> int:
    packages = sorted(p for p in PACKAGES_DIR.iterdir() if (p / "pyproject.toml").exists())
    DIST.mkdir(exist_ok=True)
    failures: list[str] = []
    for package in packages:
        print(f"== building {package.name} ==")
        result = subprocess.run(
            ["uv", "build", str(package), "--out-dir", str(DIST)],
            cwd=ROOT,
        )
        if result.returncode != 0:
            failures.append(package.name)

    print("\nArtifacts in dist/:")
    for artifact in sorted(DIST.glob("*")):
        print("  ", artifact.name)

    if failures:
        print("\nFAILED:", ", ".join(failures))
        return 1
    print(f"\nBuilt {len(packages)} packages successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
