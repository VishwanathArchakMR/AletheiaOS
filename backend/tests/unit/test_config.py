import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_rejects_unsafe_graph_name() -> None:
    with pytest.raises(ValidationError):
        Settings(falkordb_graph_name="bad-name")

