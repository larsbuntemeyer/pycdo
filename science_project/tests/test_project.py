import pytest
from .. import add_one


@pytest.mark.parametrize("x, y", [(1, 2), (2, 3), (3, 4)])
def test_add_one(x, y):
    assert y == add_one(x)
