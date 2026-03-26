import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.metadata_store import MetadataStore
from app.simple_vector_rag import SimpleVectorRAG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a lightweight vector index from processed documents.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the vector index from scratch.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = [item for item in store.load_documents() if item.status in {"processed", "imported"}]
    rag = SimpleVectorRAG(settings)
    count = rag.build_index(documents, rebuild=args.rebuild)
    print(f"Indexed {count} new chunks into {settings.simple_index_file}")


if __name__ == "__main__":
    main()
