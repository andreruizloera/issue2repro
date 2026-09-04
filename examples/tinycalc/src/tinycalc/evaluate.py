"""Evaluate simple arithmetic expressions.

Known bug (kept on purpose, this repo is a demo fixture): the tokenizer
splits "-3" into "-" and "3", so expressions with negative operands crash.
"""

import re

_TOKEN = re.compile(r"\d+|[+\-*/]")


def tokenize(expr: str) -> "list[str]":
    return _TOKEN.findall(expr)


def evaluate(expr: str) -> int:
    tokens = tokenize(expr)
    if not tokens:
        raise ValueError("empty expression")
    total = int(tokens[0])
    for i in range(1, len(tokens), 2):
        op = tokens[i]
        value = int(tokens[i + 1])
        if op == "+":
            total += value
        elif op == "-":
            total -= value
        elif op == "*":
            total *= value
        elif op == "/":
            total //= value
        else:
            raise ValueError(f"unknown operator: {op}")
    return total
