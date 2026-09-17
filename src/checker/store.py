"""Evidence bundle persistence. The seam between collection and evaluation."""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvidenceBundle

DEFAULT_ROOT = Path("evidence")


def bundle_path(root: Path, run_id: str, target_id: str) -> Path:
    return root / run_id / f"{target_id}.json"


def save_bundle(bundle: EvidenceBundle, root: Path = DEFAULT_ROOT) -> Path:
    path = bundle_path(root, bundle.run_id, bundle.target_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_bundle(path: str | Path) -> EvidenceBundle:
    return EvidenceBundle.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_run(root: Path, run_id: str) -> list[EvidenceBundle]:
    run_dir = Path(root) / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No evidence for run {run_id!r} under {root}")
    return [load_bundle(p) for p in sorted(run_dir.glob("*.json"))]


def latest_run_id(root: Path = DEFAULT_ROOT) -> str | None:
    root = Path(root)
    if not root.is_dir():
        return None
    runs = sorted((d.name for d in root.iterdir() if d.is_dir()), reverse=True)
    return runs[0] if runs else None
