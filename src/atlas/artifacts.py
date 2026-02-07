from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactWriteResult:
    rel_path: str
    sha256: str
    mime_type: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(*, artifacts_dir: Path, rel_path: str, data: bytes, mime_type: str) -> ArtifactWriteResult:
    artifacts_dir = artifacts_dir.resolve()
    out_path = (artifacts_dir / rel_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return ArtifactWriteResult(rel_path=rel_path.replace("\\", "/"), sha256=_sha256_bytes(data), mime_type=mime_type)


def write_text(*, artifacts_dir: Path, rel_path: str, text: str, mime_type: str = "text/plain") -> ArtifactWriteResult:
    data = text.encode("utf-8")
    artifacts_dir = artifacts_dir.resolve()
    out_path = (artifacts_dir / rel_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return ArtifactWriteResult(rel_path=rel_path.replace("\\", "/"), sha256=_sha256_bytes(data), mime_type=mime_type)


def write_json(*, artifacts_dir: Path, rel_path: str, obj: Any, mime_type: str = "application/json") -> ArtifactWriteResult:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2)
    return write_text(artifacts_dir=artifacts_dir, rel_path=rel_path, text=text, mime_type=mime_type)
