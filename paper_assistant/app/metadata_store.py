from pathlib import Path

from app.models import DocumentRecord, ManifestEntry
from app.utils import dump_json, load_json


class MetadataStore:
    def __init__(self, metadata_file: Path, manifest_file: Path):
        self.metadata_file = metadata_file
        self.manifest_file = manifest_file

    def load_documents(self) -> list[DocumentRecord]:
        payload = load_json(self.metadata_file, [])
        return [DocumentRecord.model_validate(item) for item in payload]

    def save_documents(self, documents: list[DocumentRecord]) -> None:
        dump_json(self.metadata_file, [item.model_dump(mode="json") for item in documents])

    def upsert_document(self, record: DocumentRecord) -> None:
        documents = self.load_documents()
        by_id = {item.id: item for item in documents}
        by_id[record.id] = record
        ordered = sorted(by_id.values(), key=lambda item: item.filename.lower())
        self.save_documents(ordered)

    def find_by_checksum(self, checksum: str) -> DocumentRecord | None:
        for record in self.load_documents():
            if record.checksum == checksum:
                return record
        return None

    def filter_documents(
        self,
        keyword: str | None = None,
        tag: str | None = None,
        year: int | None = None,
        status: str | None = None,
    ) -> list[DocumentRecord]:
        documents = self.load_documents()
        results: list[DocumentRecord] = []
        keyword_lower = keyword.lower() if keyword else None
        tag_lower = tag.lower() if tag else None

        for record in documents:
            if keyword_lower:
                haystack = " ".join([record.title, record.filename, *record.tags]).lower()
                if keyword_lower not in haystack:
                    continue
            if tag_lower and tag_lower not in {item.lower() for item in record.tags}:
                continue
            if year is not None and record.year != year:
                continue
            if status and record.status != status:
                continue
            results.append(record)
        return results

    def get_manifest_entry(self, path: Path) -> ManifestEntry | None:
        payload = load_json(self.manifest_file, [])
        entries = payload.get("documents", payload) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return None
        resolved_path = str(path.resolve())
        for item in entries:
            entry = ManifestEntry.model_validate(item)
            if entry.filepath and str(Path(entry.filepath).resolve()) == resolved_path:
                return entry
            if entry.file_name and entry.file_name == path.name:
                return entry
        return None
