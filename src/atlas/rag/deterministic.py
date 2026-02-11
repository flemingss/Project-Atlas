from __future__ import annotations

import hashlib
import uuid


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deterministic_chunk_id(
    *,
    tenant_id: str,
    project_id: str,
    corpus_id: str,
    doc_id: str,
    doc_version: str,
    content_hash: str,
    chunk_index: int,
) -> str:
    # Qdrant point IDs must be an integer or a UUID.
    # We derive a deterministic UUID from a sha256 digest of stable inputs.
    raw = f"{tenant_id}:{project_id}:{corpus_id}:{doc_id}:{doc_version}:{chunk_index}:{content_hash}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    # Use the first 16 bytes of the digest; set version bits so it's a valid UUID string.
    uid = uuid.UUID(bytes=digest[:16], version=5)
    return str(uid)
