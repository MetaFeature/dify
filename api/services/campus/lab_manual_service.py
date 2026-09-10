"""Store and read original HTML learning documents for experiment tracks.

A manual belongs to one experiment track and is a list of ordered chapters.
Administrators upload files; students read only the published ones. New uploads
are stored as bytes and served from the dedicated manual origin unchanged.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    MANUAL_TRACKS,
    CampusAuditEvent,
    CampusLabManualChapter,
    CampusLabManualImage,
    ExperimentTrack,
    LabManualChapterStatus,
)
from services.campus.errors import CampusValidationError

MAX_TITLE_LENGTH = 255
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_URL_PREFIX = "/console/api/campus/lab-manuals/images"

# Raster formats only, keyed by the bytes that begin the file. SVG is absent on
# purpose: browsers render it as an image, but it is a document that can carry
# script, and it would arrive through the one path that does not sanitize.
IMAGE_SIGNATURES: Mapping[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


class ManualImageStorage(Protocol):
    def save(self, filename: str, data: bytes) -> None: ...

    def load_once(self, filename: str) -> bytes: ...

    def delete(self, filename: str) -> None: ...


@dataclass(frozen=True)
class UploadedImage:
    """A stored image and the same-origin URL a chapter references it by."""

    id: str
    url: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class ManualImage:
    """One image's bytes, ready to serve."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ChapterOutcome:
    """Metadata for one stored original HTML document."""

    id: str
    track: ExperimentTrack
    title: str
    original_filename: str
    size_bytes: int
    position: int
    status: LabManualChapterStatus


@dataclass(frozen=True)
class LearningDocument:
    """The exact uploaded bytes and filename returned to a reader."""

    data: bytes
    filename: str


class LabManualService:
    _session: Session
    _storage: ManualImageStorage | None

    def __init__(self, *, session: Session, storage: ManualImageStorage | None = None) -> None:
        self._session = session
        self._storage = storage

    def add_image(
        self,
        track: ExperimentTrack,
        *,
        data: bytes,
        mime_type: str,
        actor_account_id: str | None = None,
    ) -> UploadedImage:
        """Store one image for a track's manual and return how to reference it."""
        self._require_manual_track(track)
        declared = mime_type.split(";", 1)[0].strip().lower()
        if not data:
            raise CampusValidationError("Image upload is empty")
        if len(data) > MAX_IMAGE_BYTES:
            raise CampusValidationError(f"Image upload is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        signatures = IMAGE_SIGNATURES.get(declared)
        if signatures is None:
            raise CampusValidationError(f"Unsupported image type: {declared}")
        if not any(data.startswith(signature) for signature in signatures):
            raise CampusValidationError(f"Image content does not match the declared type {declared}")

        image = CampusLabManualImage(
            track=track,
            storage_key="",
            mime_type=declared,
            size_bytes=len(data),
        )
        self._session.add(image)
        self._session.flush()
        image.storage_key = f"campus/lab-manuals/{track.value}/{image.id}{self._extension(declared)}"
        self._store().save(image.storage_key, data)
        self._record_image(
            actor_account_id,
            image.id,
            {"track": track.value, "mime_type": declared, "size_bytes": len(data)},
        )
        self._session.commit()
        return UploadedImage(
            id=image.id,
            url=f"{IMAGE_URL_PREFIX}/{image.id}",
            mime_type=declared,
            size_bytes=len(data),
        )

    def image(self, image_id: str) -> ManualImage:
        """Read one stored image."""
        image = self._session.scalar(select(CampusLabManualImage).where(CampusLabManualImage.id == image_id))
        if image is None:
            raise CampusValidationError("Lab manual image was not found")
        return ManualImage(data=self._store().load_once(image.storage_key), mime_type=image.mime_type)

    def _record_image(self, actor_account_id: str | None, image_id: str, details: Mapping[str, object]) -> None:
        """Record an image upload without committing; see _record for the why."""
        if actor_account_id is None:
            return
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="lab_manual.image_added",
                target_type="lab_manual_image",
                target_id=image_id,
                details_json=json.dumps(dict(details), separators=(",", ":"), ensure_ascii=False),
            )
        )

    def _store(self) -> ManualImageStorage:
        if self._storage is None:
            raise CampusValidationError("Lab manual image storage is not configured")
        return self._storage

    @staticmethod
    def _extension(mime_type: str) -> str:
        return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(
            mime_type, ""
        )

    def create_chapter(
        self,
        track: ExperimentTrack,
        *,
        filename: str,
        data: bytes,
        actor_account_id: str | None = None,
    ) -> ChapterOutcome:
        """Append an original HTML file as a draft document."""
        self._require_manual_track(track)
        clean_filename = self._require_filename(filename)
        document_data = self._require_document_data(data)
        chapter = CampusLabManualChapter(
            track=track,
            title=clean_filename,
            body_html="",
            document_data=document_data,
            original_filename=clean_filename,
            document_size_bytes=len(document_data),
            position=self._next_position(track),
            status=LabManualChapterStatus.DRAFT,
        )
        self._session.add(chapter)
        self._session.flush()
        self._record(
            actor_account_id,
            "lab_manual.chapter_created",
            chapter,
            {
                "track": track.value,
                "position": chapter.position,
                "filename": clean_filename,
                "size_bytes": len(document_data),
            },
        )
        self._session.commit()
        return self._outcome(chapter)

    def update_chapter(
        self,
        chapter_id: str,
        *,
        filename: str,
        data: bytes,
        actor_account_id: str | None = None,
    ) -> ChapterOutcome:
        """Replace a document's original file, leaving its place and status alone."""
        chapter = self._require_chapter(chapter_id)
        clean_filename = self._require_filename(filename)
        document_data = self._require_document_data(data)
        chapter.title = clean_filename
        chapter.body_html = ""
        chapter.document_data = document_data
        chapter.original_filename = clean_filename
        chapter.document_size_bytes = len(document_data)
        self._record(
            actor_account_id,
            "lab_manual.chapter_updated",
            chapter,
            {"filename": clean_filename, "size_bytes": len(document_data)},
        )
        self._session.commit()
        return self._outcome(chapter)

    def set_chapter_status(
        self, chapter_id: str, status: LabManualChapterStatus, *, actor_account_id: str | None = None
    ) -> ChapterOutcome:
        """Publish a chapter to students, or withdraw it back to a draft."""
        chapter = self._require_chapter(chapter_id)
        chapter.status = status
        self._record(actor_account_id, "lab_manual.chapter_status_changed", chapter, {"status": status.value})
        self._session.commit()
        return self._outcome(chapter)

    def move_chapter(self, chapter_id: str, *, position: int, actor_account_id: str | None = None) -> None:
        """Move one chapter within its own track and renumber that track."""
        chapter = self._require_chapter(chapter_id)
        siblings = [other for other in self._chapters(chapter.track) if other.id != chapter.id]
        target = max(1, min(position, len(siblings) + 1))
        siblings.insert(target - 1, chapter)
        self._renumber(siblings)
        self._record(actor_account_id, "lab_manual.chapter_moved", chapter, {"position": chapter.position})
        self._session.commit()

    def delete_chapter(self, chapter_id: str, *, actor_account_id: str | None = None) -> None:
        """Remove a chapter and close the gap in its track's numbering."""
        chapter = self._require_chapter(chapter_id)
        track = chapter.track
        self._record(actor_account_id, "lab_manual.chapter_deleted", chapter, {"track": track.value})
        self._session.delete(chapter)
        self._session.flush()
        self._renumber(self._chapters(track))
        self._session.commit()

    def chapter(self, chapter_id: str) -> CampusLabManualChapter:
        """Read one chapter, whatever its status."""
        return self._require_chapter(chapter_id)

    def document(self, chapter_id: str) -> LearningDocument:
        """Return the uploaded file without decoding or rewriting it."""
        chapter = self._require_chapter(chapter_id)
        if chapter.document_data is not None:
            data = chapter.document_data
        else:
            data = chapter.body_html.encode("utf-8")
        return LearningDocument(data=data, filename=chapter.original_filename or f"{chapter.title}.html")

    def all_chapters(self, track: ExperimentTrack) -> Sequence[CampusLabManualChapter]:
        """Every chapter of one track, drafts included, in reading order."""
        return self._chapters(track)

    def published_chapters(self, track: ExperimentTrack) -> Sequence[CampusLabManualChapter]:
        """The chapters a student may read, in reading order."""
        return [chapter for chapter in self._chapters(track) if chapter.status is LabManualChapterStatus.PUBLISHED]

    def _chapters(self, track: ExperimentTrack) -> list[CampusLabManualChapter]:
        return list(
            self._session.scalars(
                select(CampusLabManualChapter)
                .where(CampusLabManualChapter.track == track)
                .order_by(CampusLabManualChapter.position, CampusLabManualChapter.id)
            )
        )

    def _next_position(self, track: ExperimentTrack) -> int:
        return len(self._chapters(track)) + 1

    def _record(
        self,
        actor_account_id: str | None,
        action: str,
        chapter: CampusLabManualChapter,
        details: Mapping[str, object],
    ) -> None:
        """Record one authoring action without committing.

        Publishing decides what every student sees, so it belongs in the same
        attributable trail as roster and allowance changes (ADR-0009). File
        bytes are deliberately absent: the trail records what happened, not a
        second copy of the document.
        """
        if actor_account_id is None:
            return
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="lab_manual_chapter",
                target_id=chapter.id,
                details_json=json.dumps(dict(details), separators=(",", ":"), ensure_ascii=False),
            )
        )

    @staticmethod
    def _renumber(chapters: Sequence[CampusLabManualChapter]) -> None:
        for index, chapter in enumerate(chapters, start=1):
            chapter.position = index

    def _require_chapter(self, chapter_id: str) -> CampusLabManualChapter:
        chapter = self._session.scalar(select(CampusLabManualChapter).where(CampusLabManualChapter.id == chapter_id))
        if chapter is None:
            raise CampusValidationError("Lab manual chapter was not found")
        return chapter

    @staticmethod
    def _require_manual_track(track: ExperimentTrack) -> None:
        if track not in MANUAL_TRACKS:
            raise CampusValidationError(f"Experiment track {track.value} has no lab manual")

    @staticmethod
    def _require_filename(filename: str) -> str:
        clean = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not clean:
            raise CampusValidationError("Learning document filename is required")
        if len(clean) > MAX_TITLE_LENGTH:
            raise CampusValidationError(f"Learning document filename is longer than {MAX_TITLE_LENGTH} characters")
        if not clean.lower().endswith((".html", ".htm")):
            raise CampusValidationError("Learning document must be an HTML file")
        return clean

    @staticmethod
    def _require_document_data(data: bytes) -> bytes:
        if not data:
            raise CampusValidationError("Learning document file is empty")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CampusValidationError(
                f"Learning document file is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB"
            )
        return data

    @staticmethod
    def _outcome(chapter: CampusLabManualChapter) -> ChapterOutcome:
        return ChapterOutcome(
            id=chapter.id,
            track=chapter.track,
            title=chapter.title,
            original_filename=chapter.original_filename or f"{chapter.title}.html",
            size_bytes=chapter.document_size_bytes or len(chapter.body_html.encode("utf-8")),
            position=chapter.position,
            status=chapter.status,
        )
