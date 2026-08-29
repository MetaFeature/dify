import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.campus import (
    CampusLabManualChapter,
    ExperimentTrack,
    LabManualChapterStatus,
)
from services.campus.errors import CampusValidationError
from services.campus.lab_manual_service import LabManualService

TRACK = ExperimentTrack.DEEP_LEARNING


@pytest.fixture
def manual_session(sqlite_engine) -> Session:
    CampusLabManualChapter.metadata.create_all(sqlite_engine, tables=[CampusLabManualChapter.__table__])
    with Session(sqlite_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def manuals(manual_session: Session) -> LabManualService:
    return LabManualService(session=manual_session)


def test_a_new_chapter_lands_at_the_end_as_a_draft(manuals: LabManualService) -> None:
    first = manuals.create_chapter(TRACK, title="装环境", raw_html="<p>先装 conda</p>")
    second = manuals.create_chapter(TRACK, title="跑第一个实验", raw_html="<p>再跑训练</p>")

    assert (first.position, second.position) == (1, 2)
    assert first.status is LabManualChapterStatus.DRAFT
    assert first.body_html == "<p>先装 conda</p>"


def test_creating_a_chapter_reports_what_sanitizing_removed(manuals: LabManualService) -> None:
    # An administrator whose formatting vanished needs to be told, or the page
    # just looks broken to them.
    outcome = manuals.create_chapter(
        TRACK,
        title="装环境",
        raw_html='<style>p{color:red}</style><p style="margin:0">先装 conda</p><script>x()</script>',
    )

    assert outcome.body_html == "<p>先装 conda</p>"
    assert outcome.removed == {"style": 1, "script": 1, "style attribute": 1}


def test_a_chapter_with_nothing_readable_is_rejected(manuals: LabManualService) -> None:
    with pytest.raises(CampusValidationError, match="no readable content"):
        manuals.create_chapter(TRACK, title="空的", raw_html="<style>p{}</style>")


def test_a_chapter_needs_a_title(manuals: LabManualService) -> None:
    with pytest.raises(CampusValidationError, match="title is required"):
        manuals.create_chapter(TRACK, title="   ", raw_html="<p>x</p>")


def test_the_large_model_track_has_no_manual(manuals: LabManualService) -> None:
    # That track is completed in Dify, so publishing a manual for it would
    # advertise a path the platform does not serve.
    with pytest.raises(CampusValidationError, match="no lab manual"):
        manuals.create_chapter(ExperimentTrack.LARGE_MODEL, title="x", raw_html="<p>x</p>")


def test_editing_a_chapter_replaces_its_body_and_keeps_its_place(manuals: LabManualService) -> None:
    created = manuals.create_chapter(TRACK, title="装环境", raw_html="<p>旧</p>")
    manuals.create_chapter(TRACK, title="第二章", raw_html="<p>x</p>")

    updated = manuals.update_chapter(created.id, title="装环境（修订）", raw_html="<p>新</p>")

    assert updated.position == 1
    assert updated.title == "装环境（修订）"
    assert updated.body_html == "<p>新</p>"


def test_publishing_and_unpublishing_a_chapter(manuals: LabManualService) -> None:
    created = manuals.create_chapter(TRACK, title="装环境", raw_html="<p>x</p>")

    assert manuals.set_chapter_status(created.id, LabManualChapterStatus.PUBLISHED).status is (
        LabManualChapterStatus.PUBLISHED
    )
    assert manuals.set_chapter_status(created.id, LabManualChapterStatus.DRAFT).status is (LabManualChapterStatus.DRAFT)


def test_students_see_only_published_chapters_in_order(manuals: LabManualService) -> None:
    one = manuals.create_chapter(TRACK, title="一", raw_html="<p>1</p>")
    manuals.create_chapter(TRACK, title="二（草稿）", raw_html="<p>2</p>")
    three = manuals.create_chapter(TRACK, title="三", raw_html="<p>3</p>")
    manuals.set_chapter_status(three.id, LabManualChapterStatus.PUBLISHED)
    manuals.set_chapter_status(one.id, LabManualChapterStatus.PUBLISHED)

    published = manuals.published_chapters(TRACK)

    assert [chapter.title for chapter in published] == ["一", "三"]


def test_administrators_see_drafts_too(manuals: LabManualService) -> None:
    manuals.create_chapter(TRACK, title="一", raw_html="<p>1</p>")
    manuals.create_chapter(TRACK, title="二（草稿）", raw_html="<p>2</p>")

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["一", "二（草稿）"]


def test_a_track_with_no_published_chapter_reads_as_empty(manuals: LabManualService) -> None:
    manuals.create_chapter(TRACK, title="草稿", raw_html="<p>1</p>")

    assert manuals.published_chapters(TRACK) == []
    assert manuals.published_chapters(ExperimentTrack.AGENT) == []


def test_reordering_moves_one_chapter_and_renumbers_the_rest(manuals: LabManualService) -> None:
    one = manuals.create_chapter(TRACK, title="一", raw_html="<p>1</p>")
    two = manuals.create_chapter(TRACK, title="二", raw_html="<p>2</p>")
    three = manuals.create_chapter(TRACK, title="三", raw_html="<p>3</p>")

    manuals.move_chapter(three.id, position=1)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["三", "一", "二"]
    assert [chapter.position for chapter in manuals.all_chapters(TRACK)] == [1, 2, 3]
    assert {one.id, two.id, three.id} == {chapter.id for chapter in manuals.all_chapters(TRACK)}


def test_reordering_clamps_a_position_outside_the_manual(manuals: LabManualService) -> None:
    one = manuals.create_chapter(TRACK, title="一", raw_html="<p>1</p>")
    manuals.create_chapter(TRACK, title="二", raw_html="<p>2</p>")

    manuals.move_chapter(one.id, position=99)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["二", "一"]


def test_reordering_never_crosses_tracks(manuals: LabManualService) -> None:
    deep = manuals.create_chapter(ExperimentTrack.DEEP_LEARNING, title="深度", raw_html="<p>1</p>")
    agent = manuals.create_chapter(ExperimentTrack.AGENT, title="智能体", raw_html="<p>2</p>")

    manuals.move_chapter(deep.id, position=1)

    assert manuals.all_chapters(ExperimentTrack.AGENT)[0].id == agent.id
    assert manuals.all_chapters(ExperimentTrack.AGENT)[0].position == 1


def test_deleting_a_chapter_closes_the_gap_it_leaves(manuals: LabManualService) -> None:
    manuals.create_chapter(TRACK, title="一", raw_html="<p>1</p>")
    two = manuals.create_chapter(TRACK, title="二", raw_html="<p>2</p>")
    manuals.create_chapter(TRACK, title="三", raw_html="<p>3</p>")

    manuals.delete_chapter(two.id)

    assert [chapter.title for chapter in manuals.all_chapters(TRACK)] == ["一", "三"]
    assert [chapter.position for chapter in manuals.all_chapters(TRACK)] == [1, 2]


def test_acting_on_a_chapter_that_is_gone_fails_clearly(manuals: LabManualService) -> None:
    for act in (
        lambda: manuals.update_chapter("00000000-0000-0000-0000-000000000000", title="x", raw_html="<p>x</p>"),
        lambda: manuals.move_chapter("00000000-0000-0000-0000-000000000000", position=1),
        lambda: manuals.delete_chapter("00000000-0000-0000-0000-000000000000"),
        lambda: manuals.set_chapter_status("00000000-0000-0000-0000-000000000000", LabManualChapterStatus.PUBLISHED),
    ):
        with pytest.raises(CampusValidationError, match="chapter was not found"):
            act()


def test_a_published_body_is_stored_sanitized_not_sanitized_on_read(
    manuals: LabManualService, manual_session: Session
) -> None:
    # Serving a chapter must be a plain string read, so the row itself has to be
    # clean. Reading the column directly is the only way to prove that.
    manuals.create_chapter(TRACK, title="x", raw_html='<p onclick="x()">hi</p><script>bad()</script>')

    stored = manual_session.scalar(select(CampusLabManualChapter.body_html))

    assert stored == "<p>hi</p>"
