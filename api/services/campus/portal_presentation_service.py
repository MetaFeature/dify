"""Draft and publish the administrator-managed student Portal presentation."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusPortalPresentation, ExperimentTrack
from services.campus.lab_manual_html import sanitize_lab_manual_html

CONTENT_KEY = "student-portal"


@dataclass(frozen=True)
class TrackPresentation:
    track: ExperimentTrack
    title: str
    description: str
    position: int


@dataclass(frozen=True)
class PortalPresentation:
    login_html: str
    tracks: tuple[TrackPresentation, ...]
    is_custom: bool


DEFAULT_PRESENTATION = PortalPresentation(
    login_html=(
        '<p class="eyebrow">CAMPUS AI WORKSPACE</p>'
        "<h1>预约你的专属<br>AI 实践空间</h1>"
        "<p>在开放时段内进入隔离的 Dify 工作区，完成课程实践、模型调用与作品导出。</p>"
    ),
    tracks=(
        TrackPresentation(ExperimentTrack.LARGE_MODEL, "大模型实验", "在 Dify 工作区完成，需预约时段", 1),
        TrackPresentation(ExperimentTrack.AGENT, "智能体实验", "在自己的电脑上完成", 2),
        TrackPresentation(ExperimentTrack.DEEP_LEARNING, "深度学习实验", "在自己的电脑上完成", 3),
        TrackPresentation(ExperimentTrack.REFERENCE, "参考资料", "课程其他参考资料", 4),
    ),
    is_custom=False,
)


class PortalPresentationService:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    def published(self) -> PortalPresentation:
        row = self._row()
        if row is None or row.published_json is None:
            return DEFAULT_PRESENTATION
        return self._decode(row.published_json, is_custom=True)

    def draft(self) -> PortalPresentation:
        row = self._row()
        if row is None:
            return DEFAULT_PRESENTATION
        return self._decode(row.draft_json, is_custom=True)

    def save_draft(
        self,
        *,
        login_html: str,
        tracks: Sequence[TrackPresentation],
        actor_account_id: str,
    ) -> PortalPresentation:
        presentation = PortalPresentation(
            login_html=sanitize_lab_manual_html(login_html).html,
            tracks=tuple(sorted(tracks, key=lambda item: item.position)),
            is_custom=True,
        )
        encoded = self._encode(presentation)
        row = self._row(for_update=True)
        if row is None:
            row = CampusPortalPresentation(
                content_key=CONTENT_KEY,
                draft_json=encoded,
                published_json=None,
                updated_by_account_id=actor_account_id,
            )
            self._session.add(row)
        else:
            row.draft_json = encoded
            row.updated_by_account_id = actor_account_id
        self._audit("portal.presentation_draft_saved", actor_account_id)
        self._session.commit()
        return presentation

    def publish(self, *, actor_account_id: str) -> PortalPresentation:
        row = self._row(for_update=True)
        if row is None:
            self.save_draft(
                login_html=DEFAULT_PRESENTATION.login_html,
                tracks=DEFAULT_PRESENTATION.tracks,
                actor_account_id=actor_account_id,
            )
            row = self._row(for_update=True)
            assert row is not None
        row.published_json = row.draft_json
        row.updated_by_account_id = actor_account_id
        self._audit("portal.presentation_published", actor_account_id)
        self._session.commit()
        published_json = row.published_json
        assert published_json is not None
        return self._decode(published_json, is_custom=True)

    def restore_default(self, *, actor_account_id: str) -> PortalPresentation:
        row = self._row(for_update=True)
        if row is not None:
            self._session.delete(row)
        self._audit("portal.presentation_default_restored", actor_account_id)
        self._session.commit()
        return DEFAULT_PRESENTATION

    def _row(self, *, for_update: bool = False) -> CampusPortalPresentation | None:
        statement = select(CampusPortalPresentation).where(CampusPortalPresentation.content_key == CONTENT_KEY)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def _audit(self, action: str, actor_account_id: str) -> None:
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action=action,
                target_type="portal_presentation",
                target_id=CONTENT_KEY,
                details_json="{}",
            )
        )

    @staticmethod
    def _encode(presentation: PortalPresentation) -> str:
        return json.dumps(
            {
                "login_html": presentation.login_html,
                "tracks": [
                    {
                        "track": item.track.value,
                        "title": item.title,
                        "description": item.description,
                        "position": item.position,
                    }
                    for item in presentation.tracks
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode(raw: str, *, is_custom: bool) -> PortalPresentation:
        value: Mapping[str, object] = json.loads(raw)
        tracks = tuple(
            TrackPresentation(
                track=ExperimentTrack(str(item["track"])),
                title=str(item["title"]),
                description=str(item["description"]),
                position=int(item["position"]),
            )
            for item in value["tracks"]  # type: ignore[union-attr]
        )
        return PortalPresentation(login_html=str(value["login_html"]), tracks=tracks, is_custom=is_custom)
