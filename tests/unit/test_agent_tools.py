import pytest

from app.agents.tools import CalculatorTool
from app.errors import AppError


def test_calculator_evaluates_only_bounded_arithmetic():
    calculator = CalculatorTool()

    assert calculator.execute("(30 - 15) * 2") == {
        "expression": "(30 - 15) * 2",
        "value": 30,
    }
    assert calculator.execute("7 / 2")["value"] == 3.5


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "(1).__class__",
        "2 ** 1000",
        "sum([1, 2])",
    ],
)
def test_calculator_rejects_code_execution_and_unbounded_operations(expression):
    with pytest.raises(AppError) as error:
        CalculatorTool().execute(expression)
    assert error.value.code == "INVALID_CALCULATION"
