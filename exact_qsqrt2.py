from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import sqrt


SQRT2 = sqrt(2.0)


@dataclass(frozen=True, order=True)
class Qsqrt2:
    """Exact element of Q(√2), stored as p + q√2 with rational p and q."""

    p: Fraction = Fraction(0)
    q: Fraction = Fraction(0)

    def __init__(self, p=0, q=0):
        object.__setattr__(self, "p", Fraction(p))
        object.__setattr__(self, "q", Fraction(q))

    @classmethod
    def from_ratio(cls, a: int, b: int, c: int = 1, d: int = 0) -> "Qsqrt2":
        """Create (a+b√2)/(c+d√2) exactly by rationalizing the denominator."""
        denominator = c * c - 2 * d * d
        if denominator == 0:
            raise ZeroDivisionError("c+d√2 must be non-zero")
        return cls(
            Fraction(a * c - 2 * b * d, denominator),
            Fraction(b * c - a * d, denominator),
        )

    def __add__(self, other) -> "Qsqrt2":
        other = _coerce(other)
        return Qsqrt2(self.p + other.p, self.q + other.q)

    __radd__ = __add__

    def __sub__(self, other) -> "Qsqrt2":
        other = _coerce(other)
        return Qsqrt2(self.p - other.p, self.q - other.q)

    def __rsub__(self, other) -> "Qsqrt2":
        return _coerce(other) - self

    def __neg__(self) -> "Qsqrt2":
        return Qsqrt2(-self.p, -self.q)

    def __mul__(self, other) -> "Qsqrt2":
        other = _coerce(other)
        return Qsqrt2(
            self.p * other.p + 2 * self.q * other.q,
            self.p * other.q + self.q * other.p,
        )

    __rmul__ = __mul__

    def reciprocal(self) -> "Qsqrt2":
        denominator = self.p * self.p - 2 * self.q * self.q
        if denominator == 0:
            raise ZeroDivisionError("division by zero in Q(√2)")
        return Qsqrt2(self.p / denominator, -self.q / denominator)

    def __truediv__(self, other) -> "Qsqrt2":
        return self * _coerce(other).reciprocal()

    def __rtruediv__(self, other) -> "Qsqrt2":
        return _coerce(other) / self

    def __float__(self) -> float:
        return float(self.p) + float(self.q) * SQRT2

    def reflect_about(self, center: "Qsqrt2") -> "Qsqrt2":
        return 2 * _coerce(center) - self

    @property
    def coefficient_complexity(self) -> int:
        """Small integer proxy for description length, not a validity test."""
        return (
            abs(self.p.numerator)
            + self.p.denominator
            + abs(self.q.numerator)
            + self.q.denominator
        )

    def expression(self) -> str:
        if self.q == 0:
            return _fraction_text(self.p)
        if self.p == 0:
            return _root_term(self.q)
        sign = "+" if self.q > 0 else "−"
        return f"{_fraction_text(self.p)}{sign}{_root_term(abs(self.q))}"


def _coerce(value) -> Qsqrt2:
    return value if isinstance(value, Qsqrt2) else Qsqrt2(value)


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _root_term(value: Fraction) -> str:
    if value == 1:
        return "√2"
    if value == -1:
        return "−√2"
    return f"{_fraction_text(value)}√2"
