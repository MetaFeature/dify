from sqlalchemy.orm import Session

from models.campus import CampusAuditEvent, CampusPortalPresentation, ExperimentTrack
from services.campus.portal_presentation_service import PortalPresentationService, TrackPresentation


def test_portal_presentation_uses_defaults_then_publishes_sanitized_content(sqlite_engine) -> None:
    CampusPortalPresentation.metadata.create_all(
        sqlite_engine,
        tables=[CampusPortalPresentation.__table__, CampusAuditEvent.__table__],
    )
    with Session(sqlite_engine, expire_on_commit=False) as session:
        service = PortalPresentationService(session=session)
        assert [item.track for item in service.published().tracks] == [
            ExperimentTrack.LARGE_MODEL,
            ExperimentTrack.AGENT,
            ExperimentTrack.DEEP_LEARNING,
        ]

        draft = service.save_draft(
            login_html='<h1 onclick="bad()">新入口</h1><script>bad()</script>',
            tracks=[
                TrackPresentation(ExperimentTrack.AGENT, "智能体", "在自己的电脑上完成", 1),
                TrackPresentation(ExperimentTrack.LARGE_MODEL, "大模型", "进入 Dify", 2),
                TrackPresentation(ExperimentTrack.DEEP_LEARNING, "深度学习", "在自己的电脑上完成", 3),
            ],
            actor_account_id="admin-1",
        )

        assert draft.login_html == "<h1>新入口</h1>"
        assert not service.published().is_custom
        published = service.publish(actor_account_id="admin-1")
        assert published.is_custom
        assert [item.track for item in published.tracks] == [
            ExperimentTrack.AGENT,
            ExperimentTrack.LARGE_MODEL,
            ExperimentTrack.DEEP_LEARNING,
        ]

        restored = service.restore_default(actor_account_id="admin-1")
        assert not restored.is_custom
        assert not service.published().is_custom
