import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.metadata_store import MetadataStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List imported local literature.")
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--tag", type=str, default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--status", type=str, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    store = MetadataStore(settings.metadata_file, settings.manifest_file)
    documents = store.filter_documents(
        keyword=args.keyword,
        tag=args.tag,
        year=args.year,
        status=args.status,
    )

    if not documents:
        print("No documents found.")
        return

    for index, document in enumerate(documents, start=1):
        tags = ", ".join(document.tags) if document.tags else "-"
        authors = ", ".join(document.authors) if document.authors else "-"
        print(f"[{index}] {document.title}")
        print(f"    status={document.status} | year={document.year or '-'} | tags={tags}")
        print(f"    authors={authors}")
        print(f"    path={document.filepath}")


if __name__ == "__main__":
    main()
