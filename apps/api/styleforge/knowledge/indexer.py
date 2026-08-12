"""Index curated knowledge Markdown into a Chroma vector store.

Chunking happens in two stages: ``##`` sections first (mirroring keyword
retrieval), then any section longer than the embedder's context window is
sub-split into overlapping pieces. Every sub-chunk is embedded with the shared
FashionCLIP text encoder and upserted into ``ChromaStore``, idempotently keyed
by ``<entry-id>:<section-index>:<sub-index>``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.integrations.embeddings.text_embedder import TextEmbedder
from styleforge.integrations.vectorstores.chroma_store import ChromaStore
from styleforge.knowledge.chunking import split_long_text
from styleforge.knowledge.ingestion import load_knowledge_documents


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def index_knowledge(
    *,
    knowledge_root: Path,
    model_dir: Path,
    persist_dir: Path,
    device: str,
    precision: str,
    batch_size: int = 64,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Embed every knowledge chunk and upsert it into the Chroma store."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    documents = load_knowledge_documents(knowledge_root)
    if not documents:
        raise RuntimeError(f"No indexed knowledge documents under {knowledge_root}")

    records: list[dict[str, Any]] = []
    for doc in documents:
        for section_index, (section, content) in enumerate(doc.sections):
            for sub_index, sub_content in enumerate(split_long_text(content)):
                records.append(
                    {
                        "id": f"{doc.id}:{section_index}:{sub_index}",
                        "kind": doc.kind,
                        "source_id": doc.id,
                        "section": section,
                        "content": sub_content,
                        "embedding": None,
                    }
                )

    embedder = TextEmbedder(model_dir, device=device, precision=precision)
    dimension = embedder.dimension
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        vectors = embedder.embed_texts([record["content"] for record in batch])
        for record, vector in zip(batch, vectors):
            record["embedding"] = vector.tolist()

    store = ChromaStore(persist_dir)
    store.upsert(records)

    report = {
        "schema_version": 1,
        "generated_at": _now(),
        "knowledge_root": str(knowledge_root.resolve()),
        "persist_dir": str(store.persist_dir),
        "documents": len(documents),
        "chunks": len(records),
        "dimension": dimension,
        "model_dir": str(model_dir.resolve()),
        "device": device,
        "precision": precision,
        "collection_count": store.count(),
    }
    if report_path is not None:
        write_json_atomic(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-root", type=Path, default=settings.knowledge_root)
    parser.add_argument("--model-dir", type=Path, default=settings.artifact_root / "models")
    parser.add_argument("--persist-dir", type=Path, default=settings.chroma_dir)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--precision",
        choices=("float32", "float16", "bfloat16"),
        default="float16",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "knowledge_index.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = index_knowledge(
        knowledge_root=args.knowledge_root,
        model_dir=args.model_dir,
        persist_dir=args.persist_dir,
        device=args.device,
        precision=args.precision,
        batch_size=args.batch_size,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
