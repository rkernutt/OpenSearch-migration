"""Integration-style test with mocked HTTP for _mget parsing."""
from unittest.mock import MagicMock, patch

from validate_migration import DestAuth, verify_ids_mget_elastic


@patch("validate_migration.requests.post")
def test_mget_counts_found_and_missing(mock_post: MagicMock) -> None:
    mock_post.return_value.json.return_value = {
        "docs": [
            {"_id": "a", "found": True},
            {"_id": "b", "found": False},
        ]
    }
    mock_post.return_value.raise_for_status = MagicMock()

    auth = DestAuth(user="u", password="p")
    found, missing = verify_ids_mget_elastic("https://elastic.example", "idx", auth, ["a", "b"])

    assert found == 1
    assert missing == ["b"]
