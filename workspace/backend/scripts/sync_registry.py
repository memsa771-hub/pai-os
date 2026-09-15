#!/usr/bin/env python3
"""Sync the canonical repo-root cloud provider catalog into workspace/backend.

The canonical, human-edited provider catalog lives at the repo root:
/cloud_providers (inference providers). The backend serves it, but its Docker
build context is workspace/backend, so a copy must live inside the backend to
ship in the image. Edit the repo-root files, then run this to update the copy.
"""
import shutil
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]          # workspace/backend
SRC_PROVIDERS = BACKEND.parents[1] / "cloud_providers"  # <repo>/cloud_providers
DST_PROVIDERS = BACKEND / "cloud_providers"


def main() -> None:
    if not SRC_PROVIDERS.is_dir():
        raise SystemExit(f"canonical provider catalog not found at {SRC_PROVIDERS}")
    DST_PROVIDERS.mkdir(exist_ok=True)
    for f in DST_PROVIDERS.glob("*.json"):
        f.unlink()
    n = 0
    for f in sorted(SRC_PROVIDERS.glob("*.json")):
        shutil.copyfile(f, DST_PROVIDERS / f.name)
        n += 1
    print(f"synced {n} files: {SRC_PROVIDERS} -> {DST_PROVIDERS}")


if __name__ == "__main__":
    main()
