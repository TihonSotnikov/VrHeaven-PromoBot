"""Валидация сумм при вводе заказов."""

import pytest

from utils import parse_amount


@pytest.mark.parametrize("raw, expected", [
    ("1500", 1500.0),
    ("1 500", 1500.0),
    ("1500,50", 1500.5),
    ("1500.50", 1500.5),
    ("0,01", 0.01),
    (" 250 ", 250.0),
    ("1 234 567", 1234567.0),
])
def test_valid_amounts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [
    "0",
    "-5",
    "-0,01",
    "abc",
    "",
    "  ",
    "12,34,56",
    "1e10",  # больше верхнего предела
    "100000000",
    "inf",
    "nan",
])
def test_invalid_amounts(raw):
    assert parse_amount(raw) is None


def test_rounding_to_kopecks():
    assert parse_amount("100,999") == 101.0
    assert parse_amount("0,005") == 0.01
