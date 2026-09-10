from pathlib import Path

MIGRATION = (
    Path(__file__).parents[3] / "migrations/versions/2026_09_10_0900-b72e8c1f4a30_store_original_learning_documents.py"
)


def test_legacy_sanitized_chapters_are_not_left_published() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "status = 'draft'" in source
    assert "Legacy chapter bodies were sanitized" in source
