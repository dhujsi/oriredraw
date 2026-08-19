from fractions import Fraction
from math import sqrt

import pytest

from exact_qsqrt2 import Qsqrt2


def test_ratio_form_is_exactly_rationalized():
    value = Qsqrt2.from_ratio(1, 1, 1, -1)
    # (1+√2)/(1-√2) = -3-2√2
    assert value == Qsqrt2(-3, -2)


def test_midpoint_and_symmetry_keep_exact_qsqrt2_coordinates():
    center = Qsqrt2(200, -200)       # 200(1-√2)
    left = Qsqrt2(0, -100)           # -100√2
    reflected = left.reflect_about(center)

    assert reflected == Qsqrt2(400, -300)
    assert float(reflected) == pytest.approx(400 - 300 * sqrt(2), abs=1e-12)


def test_fractional_coefficients_cover_general_user_ratio_input():
    value = Qsqrt2.from_ratio(3, 2, 5, 1)
    assert isinstance(value.p, Fraction)
    assert isinstance(value.q, Fraction)
    assert value * Qsqrt2(5, 1) == Qsqrt2(3, 2)


def test_large_coefficients_remain_valid_values():
    value = Qsqrt2(-34, 24)
    assert float(value) == pytest.approx(-34 + 24 * sqrt(2))
    assert value.coefficient_complexity > Qsqrt2(1, 1).coefficient_complexity
