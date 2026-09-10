import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    CampusAuditEvent,
    CampusLabManualChapter,
    CampusLabManualImage,
    ExperimentTrack,
    LabManualChapterStatus,
)
from services.campus.errors import CampusValidationError
from services.campus.lab_manual_service import LabManualService

TRACK = ExperimentTrack.DEEP_LEARNING


@pytest.fixture
def manual_session(sqlite_engine) -> Session:
    CampusLabManualChapter.metadata.create_all(
        sqlite_engine, tables=[CampusLabManualChapter.__table__, CampusAuditEvent.__table__]
    )
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def manuals(manual_session: Session) -> LabManualService:
    return LabManualService(session=manual_session)


def upload(manuals: LabManualService, name: str, body: bytes = b"<p>x</p>", **kwargs):
    return manuals.create_chapter(TRACK, filename=f"{name}.html", data=body, **kwargs)


def test_a_new_chapter_lands_at_the_end_as_a_draft(manuals: LabManualService) -> None:
    first = upload(manuals, "装环境", "<p>先装 conda</p>".encode())
    second = upload(manuals, "跑第一个实验", "<p>再跑训练</p>".encode())

    assert (first.position, second.position) == (1, 2)
    assert first.status is LabManualChapterStatus.DRAFT
    assert first.original_filename == "装环境.html"


def test_creating_a_document_preserves_every_original_byte(manuals: LabManualService) -> None:
    original = b"\xff\xfe<html><script>window.x = 1</script>\x00</html>\r\n"
    outcome = upload(manuals, "原文件", original)

    assert manuals.document(outcome.id).data == original
    assert manuals.document(outcome.id).filename == "原文件.html"
    assert outcome.size_bytes == len(original)


def test_an_empty_document_is_rejected(manuals: LabManualService) -> None:
    with pytest.raises(CampusValidationError, match="file is empty"):
        upload(manuals, "空的", b"")


def test_a_document_needs_an_html_filename(manuals: LabManualService) -> None:
    with pytest.raises(CampusValidationError, match="must be an HTML file"):
        manuals.create_chapter(TRACK, filename="notes.txt", data=b"anything")


def test_the_large_model_track_accepts_learning_documents(manuals: LabManualService) -> None:
    created = manuals.create_chapter(ExperimentTrack.LARGE_MODEL, filename="x.html", data=b"<p>x</p>")

    assert created.track is ExperimentTrack.LARGE_MODEL


def test_replacing_a_document_preserves_new_bytes_and_keeps_its_place(manuals: LabManualService) -> None:
    created = upload(manuals, "装环境", "<p>旧</p>".encode())
    upload(manuals, "第二章")

    updated = manuals.update_chapter(created.id, filename="装环境（修订）.html", data=b"\x80" + "<p>新</p>\n".encode())

    assert updated.position == 1
    assert updated.title == "装环境（修订）.html"
    assert manuals.document(created.id).data == b"\x80" + "<p>新</p>\n".encode()


def test_publishing_and_unpublishing_a_chapter(manuals: LabManualService) -> None:
    created = upload(manuals, "装环境")

    assert manuals.set_chapter_status(created.id, LabManualChapterStatus.PUBLISHED).status is (
        LabManualChapterStatus.PUBLISHED
    )
    assert manuals.set_chapter_status(created.id, LabManualChapterStatus.DRAFT).status is (LabManualChapterStatus.DRAFT)


def test_students_see_only_published_chapters_in_order(manuals: LabManualService) -> None:
    one = upload(manuals, "一", b"<p>1</p>")
    upload(manuals, "二（草稿）", b"<p>2</p>")
    three = upload(manuals, "三", b"<p>3</p>")
    manuals.set_chapter_status(three.id, LabManualChapterStatus.PUBLISHED)
    manuals.set_chapter_status(one.id, LabManualChapterStatus.PUBLISHED)

    published = manuals.published_chapters(TRACK)

    assert [chapter.title for chapter in published] == ["一.html", "三.html"]


def test_administrators_see_drafts_too(manuals: LabManualService) -> None:
    upload(manuals, "一")
    upload(manuals, "二（草稿）")

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["一.html", "二（草稿）.html"]


def test_a_track_with_no_published_chapter_reads_as_empty(manuals: LabManualService) -> None:
    upload(manuals, "草稿")

    assert manuals.published_chapters(TRACK) == []
    assert manuals.published_chapters(ExperimentTrack.AGENT) == []


def test_reordering_moves_one_chapter_and_renumbers_the_rest(manuals: LabManualService) -> None:
    one = upload(manuals, "一")
    two = upload(manuals, "二")
    three = upload(manuals, "三")

    manuals.move_chapter(three.id, position=1)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["三.html", "一.html", "二.html"]
    assert [chapter.position for chapter in manuals.all_chapters(TRACK)] == [1, 2, 3]
    assert {one.id, two.id, three.id} == {chapter.id for chapter in manuals.all_chapters(TRACK)}


def test_reordering_clamps_a_position_outside_the_manual(manuals: LabManualService) -> None:
    one = upload(manuals, "一")
    upload(manuals, "二")

    manuals.move_chapter(one.id, position=99)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["二.html", "一.html"]


def test_reordering_never_crosses_tracks(manuals: LabManualService) -> None:
    deep = manuals.create_chapter(ExperimentTrack.DEEP_LEARNING, filename="深度.html", data=b"1")
    agent = manuals.create_chapter(ExperimentTrack.AGENT, filename="智能体.html", data=b"2")

    manuals.move_chapter(deep.id, position=1)

    assert manuals.all_chapters(ExperimentTrack.AGENT)[0].id == agent.id
    assert manuals.all_chapters(ExperimentTrack.AGENT)[0].position == 1


def test_deleting_a_chapter_closes_the_gap_it_leaves(manuals: LabManualService) -> None:
    upload(manuals, "一")
    two = upload(manuals, "二")
    upload(manuals, "三")

    manuals.delete_chapter(two.id)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["一.html", "三.html"]
    assert [chapter.position for chapter in manuals.all_chapters(TRACK)] == [1, 2]


def test_acting_on_a_chapter_that_is_gone_fails_clearly(manuals: LabManualService) -> None:
    for act in (
        lambda: manuals.update_chapter("00000000-0000-0000-0000-000000000000", filename="x.html", data=b"x"),
        lambda: manuals.move_chapter("00000000-0000-0000-0000-000000000000", position=1),
        lambda: manuals.delete_chapter("00000000-0000-0000-0000-000000000000"),
        lambda: manuals.set_chapter_status("00000000-0000-0000-0000-000000000000", LabManualChapterStatus.PUBLISHED),
    ):
        with pytest.raises(CampusValidationError, match="chapter was not found"):
            act()


def test_an_interactive_file_is_stored_unchanged_for_isolated_serving(
    manuals: LabManualService, manual_session: Session
) -> None:
    original = b'\xff<p onclick="x()">hi</p><script>bad()</script>\r\n'
    upload(manuals, "x", original)

    stored = manual_session.scalar(select(CampusLabManualChapter.document_data))

    assert stored == original


def test_authoring_actions_are_attributable(manual_session: Session, manuals: LabManualService) -> None:
    # ADR-0009: a platform administrator's management actions are attributable.
    # Publishing decides what every student sees, so it belongs in the trail.
    created = upload(manuals, "装环境", actor_account_id="admin-1")
    manuals.update_chapter(created.id, filename="装环境（修订）.html", data=b"y", actor_account_id="admin-1")
    manuals.set_chapter_status(created.id, LabManualChapterStatus.PUBLISHED, actor_account_id="admin-2")
    manuals.move_chapter(created.id, position=1, actor_account_id="admin-1")
    manuals.delete_chapter(created.id, actor_account_id="admin-2")

    events = list(manual_session.scalars(select(CampusAuditEvent).order_by(CampusAuditEvent.created_at)))

    assert [event.action for event in events] == [
        "lab_manual.chapter_created",
        "lab_manual.chapter_updated",
        "lab_manual.chapter_status_changed",
        "lab_manual.chapter_moved",
        "lab_manual.chapter_deleted",
    ]
    assert {event.actor_account_id for event in events} == {"admin-1", "admin-2"}
    assert {event.target_type for event in events} == {"lab_manual_chapter"}


def test_the_audit_trail_never_stores_chapter_html(manual_session: Session, manuals: LabManualService) -> None:
    # The trail records what happened, not a second copy of the document.
    upload(manuals, "装环境", "<p>秘密正文</p>".encode(), actor_account_id="admin-1")

    event = manual_session.scalar(select(CampusAuditEvent))

    assert event is not None
    assert "秘密正文" not in (event.details_json or "")


def test_one_chapter_can_be_read_back_by_id(manuals: LabManualService) -> None:
    created = upload(manuals, "装环境")

    assert manuals.chapter(created.id).title == "装环境.html"
    with pytest.raises(CampusValidationError, match="chapter was not found"):
        manuals.chapter("00000000-0000-0000-0000-000000000000")


class FakeStorage:
    """Stands in for the Dify storage extension."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def save(self, filename: str, data: bytes) -> None:
        self.files[filename] = data

    def load_once(self, filename: str) -> bytes:
        return self.files[filename]

    def delete(self, filename: str) -> None:
        self.files.pop(filename, None)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
GIF = b"GIF89a" + b"0" * 64
WEBP = b"RIFF" + b"0" * 64

#: Real leading bytes per type, because the service checks them.
IMAGE_BYTES = {"image/png": PNG, "image/jpeg": JPEG, "image/gif": GIF, "image/webp": WEBP}


@pytest.fixture
def image_session(sqlite_engine) -> Session:
    CampusLabManualChapter.metadata.create_all(
        sqlite_engine,
        tables=[CampusLabManualChapter.__table__, CampusLabManualImage.__table__, CampusAuditEvent.__table__],
    )
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def images(image_session: Session) -> tuple[LabManualService, FakeStorage]:
    storage = FakeStorage()
    return LabManualService(session=image_session, storage=storage), storage


def test_an_uploaded_image_is_stored_and_addressed_by_a_same_origin_url(images) -> None:
    manuals, storage = images

    uploaded = manuals.add_image(TRACK, data=PNG, mime_type="image/png", actor_account_id="admin-1")

    assert uploaded.url == f"/console/api/campus/lab-manuals/images/{uploaded.id}"
    assert uploaded.url.startswith("/"), "img-src is 'self', so the URL must be same-origin"
    assert len(storage.files) == 1


def test_an_uploaded_image_can_be_read_back(images) -> None:
    manuals, _ = images
    uploaded = manuals.add_image(TRACK, data=PNG, mime_type="image/png")

    found = manuals.image(uploaded.id)

    assert found.data == PNG
    assert found.mime_type == "image/png"


def test_reading_an_image_that_does_not_exist_fails_clearly(images) -> None:
    manuals, _ = images

    with pytest.raises(CampusValidationError, match="image was not found"):
        manuals.image("00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"])
def test_only_raster_image_types_are_accepted(images, mime_type: str) -> None:
    # SVG is a document that can carry script, so it is not an image for this
    # purpose even though browsers render it as one.
    manuals, _ = images

    if mime_type == "image/svg+xml":
        with pytest.raises(CampusValidationError, match="image type"):
            manuals.add_image(TRACK, data=b"<svg onload='x()'></svg>", mime_type=mime_type)
    else:
        assert manuals.add_image(TRACK, data=IMAGE_BYTES[mime_type], mime_type=mime_type).id


def test_a_declared_type_that_the_bytes_contradict_is_rejected(images) -> None:
    # The declared content type is caller-supplied; the magic bytes are not.
    manuals, _ = images

    with pytest.raises(CampusValidationError, match="does not match"):
        manuals.add_image(TRACK, data=JPEG, mime_type="image/png")


def test_an_oversized_image_is_rejected(images) -> None:
    manuals, _ = images

    with pytest.raises(CampusValidationError, match="larger than"):
        manuals.add_image(TRACK, data=PNG + b"0" * (5 * 1024 * 1024), mime_type="image/png")


def test_an_empty_upload_is_rejected(images) -> None:
    manuals, _ = images

    with pytest.raises(CampusValidationError, match="empty"):
        manuals.add_image(TRACK, data=b"", mime_type="image/png")


def test_uploading_an_image_is_attributable(image_session: Session, images) -> None:
    manuals, _ = images

    manuals.add_image(TRACK, data=PNG, mime_type="image/png", actor_account_id="admin-1")

    event = image_session.scalar(select(CampusAuditEvent))
    assert event is not None
    assert event.action == "lab_manual.image_added"
    assert event.target_type == "lab_manual_image"


def test_the_large_model_track_accepts_learning_document_images(images) -> None:
    manuals, _ = images

    uploaded = manuals.add_image(ExperimentTrack.LARGE_MODEL, data=PNG, mime_type="image/png")

    assert uploaded.mime_type == "image/png"
