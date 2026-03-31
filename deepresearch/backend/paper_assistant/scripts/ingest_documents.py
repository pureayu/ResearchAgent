import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.document_loader import DocumentExtractionError, extract_document, scan_documents
from app.metadata_store import MetadataStore
from app.models import DocumentRecord
from app.utils import dump_json, now_iso, sha256_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan local documents, extract chunks, and update local metadata."
    )
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override data/raw directory.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N documents.")
    parser.add_argument("--force", action="store_true", help="Reprocess duplicated files with the same checksum.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and extract documents without writing metadata.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    raw_dir = args.raw_dir or settings.raw_dir
    store = MetadataStore(settings.metadata_file, settings.manifest_file)

    supported, skipped = scan_documents(raw_dir)
    if args.limit:
        supported = supported[: args.limit]

    print(f"Found {len(supported)} supported documents in {raw_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} unsupported files")

    pending_records: list[DocumentRecord] = []

    for path in supported:
        checksum_record = store.find_by_checksum(sha256_file(path))
        if checksum_record and not args.force:
            print(f"[skip] duplicate: {path.name}")
            continue

        try:
            processed = extract_document(path)
            manifest = store.get_manifest_entry(path)
            if manifest and manifest.title:
                processed.title = manifest.title

            processed_path = settings.processed_dir / f"{processed.id}.json"
            record = DocumentRecord(
                id=processed.id,
                filename=processed.filename,
                title=processed.title,
                authors=manifest.authors if manifest else [],
                year=manifest.year if manifest else None,
                tags=manifest.tags if manifest else [],
                filepath=processed.filepath,
                filetype=processed.filetype,
                checksum=processed.checksum,
                status="processed",
                text_chars=len(processed.text),
                chunk_count=len(processed.chunks),
                imported_at=now_iso(),
                abstract=manifest.abstract if manifest else None,
                notes=manifest.notes if manifest else None,
                processed_path=str(processed_path),
            )
            pending_records.append(record)

            if not args.dry_run:
                dump_json(processed_path, processed.model_dump(mode="json"))
            print(f"[ok] extracted: {path.name} ({len(processed.chunks)} chunks)")
        except DocumentExtractionError as exc:
            print(f"[fail] {path.name}: {exc}")
            pending_records.append(
                DocumentRecord(
                    id=f"failed-{path.stem}",
                    filename=path.name,
                    title=path.stem,
                    filepath=str(path.resolve()),
                    filetype=path.suffix.lower().lstrip("."),
                    checksum="",
                    status="failed",
                    imported_at=now_iso(),
                    last_error=str(exc),
                )
            )

    if args.dry_run:
        print("Dry run completed. No processed artifacts or metadata were written.")
        return

    for record in pending_records:
        store.upsert_document(record)

    print(f"Done. Updated metadata file: {settings.metadata_file}")


if __name__ == "__main__":
    main()
