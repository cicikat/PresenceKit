"""Public transcript line counting preserves physical offsets and full scope."""
import pytest

from core.activity import transcript
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.mark.parametrize("content,expected", [
    (b"", 0),
    (b'{"type":"user_chat","text":"fixture"}\n', 1),
    (b'\nnot-json\n \n{"text":"last, no newline"}', 4),
    (b"one\r\ntwo\r\n", 2),
    (b"one\rtwo\rthree", 3),
    (b"{}\n" * 503, 503),
])
def test_count_lines_counts_all_physical_lines_without_modifying_file(sandbox, content, expected):
    path = transcript.path_for_session(TEST_CHAR_ID, "fixture-owner", "chess", "fixture-session")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert transcript.count_lines(TEST_CHAR_ID, "fixture-owner", "chess", "fixture-session") == expected
    assert path.read_bytes() == content


def test_count_lines_missing_file_is_zero_without_creating_it(sandbox):
    path = transcript.path_for_session(TEST_CHAR_ID, "fixture-owner", "chess", "absent-session")
    assert not path.exists()
    assert transcript.count_lines(TEST_CHAR_ID, "fixture-owner", "chess", "absent-session") == 0
    assert not path.exists()


@pytest.mark.parametrize("other_scope", [
    ("other-character", "fixture-owner", "chess", "fixture-session"),
    (TEST_CHAR_ID, "other-owner", "chess", "fixture-session"),
    (TEST_CHAR_ID, "fixture-owner", "gomoku", "fixture-session"),
    (TEST_CHAR_ID, "fixture-owner", "chess", "other-session"),
])
def test_count_lines_does_not_cross_any_scope_coordinate(sandbox, other_scope):
    scope = (TEST_CHAR_ID, "fixture-owner", "chess", "fixture-session")
    transcript.append_entry(*scope, {"type": "user_chat", "text": "fixture"})
    transcript.append_entry(*scope, {"type": "assistant_chat", "text": "response"})
    assert transcript.count_lines(*scope) == 2
    assert transcript.count_lines(*other_scope) == 0
