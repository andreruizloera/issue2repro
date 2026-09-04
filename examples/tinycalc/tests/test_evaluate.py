from tinycalc import evaluate


def test_addition():
    assert evaluate("2 + 3") == 5


def test_multiplication():
    assert evaluate("4 * 2") == 8


def test_negative_operand():
    assert evaluate("2 + -3") == -1
