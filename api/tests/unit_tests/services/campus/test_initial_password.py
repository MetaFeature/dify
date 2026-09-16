import pytest

from services.campus.errors import CampusValidationError
from services.campus.student_service import derive_initial_password


def test_derives_password_from_first_name_character_pinyin_and_number_suffix() -> None:
    assert derive_initial_password("张三", "20260001") == "zhang0001"
    assert derive_initial_password("李二", "20261234") == "li1234"


def test_uses_only_the_first_character_of_a_compound_name() -> None:
    assert derive_initial_password("欧阳娜娜", "20260099") == "ou0099"


@pytest.mark.parametrize(
    ("display_name", "expected_head"),
    [
        ("翟一", "zhai"),
        ("单二", "shan"),
        ("仇三", "qiu"),
        ("区四", "ou"),
        ("朴五", "piao"),
    ],
)
def test_prefers_the_surname_reading_for_polyphonic_characters(display_name: str, expected_head: str) -> None:
    assert derive_initial_password(display_name, "20260007") == f"{expected_head}0007"


def test_a_latin_name_uses_the_account_suffix_alone() -> None:
    # No Chinese character means no pinyin head to prefix.
    assert derive_initial_password("Alice", "20260001") == "0001"


@pytest.mark.parametrize("display_name", ["•张三", "—", "   "])
def test_falls_back_to_the_number_suffix_when_no_latin_reading_exists(display_name: str) -> None:
    assert derive_initial_password(display_name, "20260001") == "0001"


def test_rejects_student_numbers_shorter_than_four_characters() -> None:
    with pytest.raises(CampusValidationError, match="at least four characters"):
        derive_initial_password("张三", "123")
