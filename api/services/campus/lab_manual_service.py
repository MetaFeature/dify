"""Author and read the lab manuals for the non-Dify experiment tracks.

A manual belongs to one experiment track and is a list of ordered chapters.
Administrators author them; students read only the published ones. Chapter HTML
is sanitized once here, on the way in, so reading a chapter never re-parses
untrusted markup.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    MANUAL_TRACKS,
    CampusAuditEvent,
    CampusLabManualChapter,
    ExperimentTrack,
    LabManualChapterStatus,
)
from services.campus.errors import CampusValidationError
from services.campus.lab_manual_html import sanitize_lab_manual_html

MAX_TITLE_LENGTH = 255


@dataclass(frozen=True)
class ChapterOutcome:
    """A stored chapter, plus what sanitizing took out of the upload."""

    id: str
    track: ExperimentTrack
    title: str
    body_html: str
    position: int
    status: LabManualChapterStatus
    removed: Mapping[str, int]


class LabManualService:
    _session: Session

    def __init__(self, *, session: Session) -> None:
        self._session = session

    def create_chapter(
        self, track: ExperimentTrack, *, title: str, raw_html: str, actor_account_id: str | None = None
    ) -> ChapterOutcome:
        """Append a draft chapter to one track's manual."""
        self._require_manual_track(track)
        clean_title = self._require_title(title)
        sanitized = sanitize_lab_manual_html(raw_html)
        chapter = CampusLabManualChapter(
            track=track,
            title=clean_title,
            body_html=sanitized.html,
            position=self._next_position(track),
            status=LabManualChapterStatus.DRAFT,
        )
        self._session.add(chapter)
        self._session.flush()
        self._record(
            actor_account_id,
            "lab_manual.chapter_created",
            chapter,
            {"track": track.value, "position": chapter.position, "removed": dict(sanitized.removed)},
        )
        self._session.commit()
        return self._outcome(chapter, sanitized.removed)

    def update_chapter(
        self, chapter_id: str, *, title: str, raw_html: str, actor_account_id: str | None = None
    ) -> ChapterOutcome:
        """Replace a chapter's title and body, leaving its place and status alone."""
        chapter = self._require_chapter(chapter_id)
        clean_title = self._require_title(title)
        sanitized = sanitize_lab_manual_html(raw_html)
        chapter.title = clean_title
        chapter.body_html = sanitized.html
        self._record(
            actor_account_id,
            "lab_manual.chapter_updated",
            chapter,
            {"removed": dict(sanitized.removed)},
        )
        self._session.commit()
        return self._outcome(chapter, sanitized.removed)

    def set_chapter_status(
        self, chapter_id: str, status: LabManualChapterStatus, *, actor_account_id: str | None = None
    ) -> ChapterOutcome:
        """Publish a chapter to students, or withdraw it back to a draft."""
        chapter = self._require_chapter(chapter_id)
        chapter.status = status
        self._record(actor_account_id, "lab_manual.chapter_status_changed", chapter, {"status": status.value})
        self._session.commit()
        return self._outcome(chapter, {})

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
        attributable trail as roster and allowance changes (ADR-0009). Chapter
        HTML is deliberately absent: the trail records what happened, not a
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
    def _require_title(title: str) -> str:
        clean = title.strip()
        if not clean:
            raise CampusValidationError("Lab manual chapter title is required")
        if len(clean) > MAX_TITLE_LENGTH:
            raise CampusValidationError(f"Lab manual chapter title is longer than {MAX_TITLE_LENGTH} characters")
        return clean

    @staticmethod
    def _outcome(chapter: CampusLabManualChapter, removed: Mapping[str, int]) -> ChapterOutcome:
        return ChapterOutcome(
            id=chapter.id,
            track=chapter.track,
            title=chapter.title,
            body_html=chapter.body_html,
            position=chapter.position,
            status=chapter.status,
            removed=removed,
        )
