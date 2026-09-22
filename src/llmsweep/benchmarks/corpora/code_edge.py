"""Twenty function-level edge-case tasks. Hidden checks stay in the controller."""

# Corpus prompts and reference solutions are data, not wrapped prose.
# ruff: noqa: E501, RUF001

from __future__ import annotations

from typing import Any

from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import TaskSpec

_RAISE = {"raises": "ValueError"}


def _case(*parts: Any) -> list[Any]:
    *args, expected = parts
    return [list(args), expected]


def _task(
    task_id: str,
    difficulty: str,
    partition: str,
    prompt: str,
    function: str,
    tests: list[list[Any]],
    reference: str,
    incorrect: str,
) -> TaskSpec:
    body = {"function": function, "tests": tests, "evaluator": "code-edge/1", "prompt": prompt}
    return TaskSpec(
        pack_id="builtin",
        pack_version="1",
        suite_id="code-edge",
        task_id=task_id,
        prompt=(
            "Return one fenced python block and no other text. "
            + prompt
            + " Use the exact function name."
        ),
        evaluator_id="code-edge",
        evaluator_version="1",
        category="coding",
        difficulty=difficulty,
        partition=partition,
        requires_tools=False,
        execution="workflow",
        max_turns=1,
        max_tokens=2048,
        task_seconds=120,
        content_digest=fingerprint(body),
        single_turn=True,
        payload={
            "function": function,
            "tests": tests,
            "reference": reference,
            "incorrect": incorrect,
        },
    )


def code_edge_tasks() -> tuple[TaskSpec, ...]:
    return (
        _task(
            "ce-01",
            "easy",
            "dev",
            "Define sum_items(items) for a list of ints. The empty list sums to 0.",
            "sum_items",
            [_case([], 0), _case([1, 2, 3], 6), _case([-1, 1], 0)],
            "def sum_items(items):\n    return sum(items)\n",
            "def sum_items(items):\n    return sum(items[1:])\n",
        ),
        _task(
            "ce-02",
            "easy",
            "dev",
            "Define char_count(text) as the number of Unicode code points, not UTF-8 bytes.",
            "char_count",
            [_case("", 0), _case("café", 4), _case("a😀b", 3)],
            "def char_count(text):\n    return len(text)\n",
            "def char_count(text):\n    return len(text.encode())\n",
        ),
        _task(
            "ce-03",
            "easy",
            "eval",
            "Define clamp(value, low, high) inclusive of both bounds.",
            "clamp",
            [_case(5, 0, 10, 5), _case(-1, 0, 10, 0), _case(12, 0, 10, 10), _case(3, 3, 3, 3)],
            "def clamp(value, low, high):\n    return min(max(value, low), high)\n",
            "def clamp(value, low, high):\n    return min(value, high)\n",
        ),
        _task(
            "ce-04",
            "medium",
            "eval",
            "Define parse_int(text) for an optional sign and digits. Raise ValueError for any other text.",
            "parse_int",
            [
                _case("0", 0),
                _case("-12", -12),
                _case("+7", 7),
                _case("1.2", _RAISE),
                _case("", _RAISE),
            ],
            "def parse_int(text):\n    if not text or text in '+-':\n        raise ValueError(text)\n    return int(text)\n",
            "def parse_int(text):\n    return int(float(text))\n",
        ),
        _task(
            "ce-05",
            "medium",
            "eval",
            "Define unique_keep(items) keeping the first copy of each value and the original order.",
            "unique_keep",
            [_case([], []), _case([1, 1, 2, 1], [1, 2]), _case(["b", "a", "b"], ["b", "a"])],
            "def unique_keep(items):\n    result = []\n    for item in items:\n        if item not in result:\n            result.append(item)\n    return result\n",
            "def unique_keep(items):\n    return sorted(set(items), key=str)\n",
        ),
        _task(
            "ce-06",
            "medium",
            "eval",
            "Define nth(items, index, default). Return default when index is outside 0..len-1. Do not treat negatives as from the end.",
            "nth",
            [
                _case([10, 20], 1, None, 20),
                _case([10], 3, 0, 0),
                _case([], 0, "x", "x"),
                _case([1], -1, 9, 9),
            ],
            "def nth(items, index, default):\n    if isinstance(index, int) and 0 <= index < len(items):\n        return items[index]\n    return default\n",
            "def nth(items, index, default):\n    return items[index]\n",
        ),
        _task(
            "ce-07",
            "medium",
            "eval",
            "Define rotate(items, steps) as a left rotation. The empty list stays empty. Large steps wrap.",
            "rotate",
            [
                _case([], 3, []),
                _case([1, 2, 3, 4], 1, [2, 3, 4, 1]),
                _case([1, 2, 3], 4, [2, 3, 1]),
            ],
            "def rotate(items, steps):\n    if not items:\n        return []\n    steps %= len(items)\n    return list(items[steps:]) + list(items[:steps])\n",
            "def rotate(items, steps):\n    return list(items[steps:]) + list(items[:steps])\n",
        ),
        _task(
            "ce-08",
            "medium",
            "eval",
            "Define is_palindrome(text) using case folding. An empty string is a palindrome.",
            "is_palindrome",
            [
                _case("kayak", True),
                _case("Kayak", True),
                _case("ab", False),
                _case("éé", True),
                _case("", True),
            ],
            "def is_palindrome(text):\n    folded = text.casefold()\n    return folded == folded[::-1]\n",
            "def is_palindrome(text):\n    return text == text[::-1]\n",
        ),
        _task(
            "ce-09",
            "easy",
            "eval",
            "Define safe_div(left, right). Return None when right is 0, otherwise the true division.",
            "safe_div",
            [_case(4, 2, 2.0), _case(1, 0, None), _case(0, 5, 0.0)],
            "def safe_div(left, right):\n    if right == 0:\n        return None\n    return left / right\n",
            "def safe_div(left, right):\n    return left / right\n",
        ),
        _task(
            "ce-10",
            "medium",
            "eval",
            "Define chunks(items, size) for a positive size. The last chunk may be shorter.",
            "chunks",
            [_case([], 2, []), _case([1, 2, 3, 4, 5], 2, [[1, 2], [3, 4], [5]])],
            "def chunks(items, size):\n    return [list(items[i:i + size]) for i in range(0, len(items), size)]\n",
            "def chunks(items, size):\n    return [list(items[:size])]\n",
        ),
        _task(
            "ce-11",
            "easy",
            "eval",
            "Define flatten_once(rows) flattening exactly one level.",
            "flatten_once",
            [_case([[1, 2], [3]], [1, 2, 3]), _case([[[1, 2]]], [[1, 2]]), _case([], [])],
            "def flatten_once(rows):\n    result = []\n    for row in rows:\n        result.extend(row)\n    return result\n",
            "def flatten_once(rows):\n    return list(rows[0]) if rows else []\n",
        ),
        _task(
            "ce-12",
            "medium",
            "eval",
            "Define histogram(items) mapping each value to its count. Do not insert zero counts.",
            "histogram",
            [_case([], {}), _case(["a", "a", "b"], {"a": 2, "b": 1})],
            "def histogram(items):\n    counts = {}\n    for item in items:\n        counts[item] = counts.get(item, 0) + 1\n    return counts\n",
            "def histogram(items):\n    return {items[-1]: 1} if items else {}\n",
        ),
        _task(
            "ce-13",
            "medium",
            "eval",
            "Define strip_blank(lines) dropping lines that are empty after stripping. Keep other text unchanged.",
            "strip_blank",
            [_case(["a", " ", "b"], ["a", "b"]), _case([""], []), _case([" x "], [" x "])],
            "def strip_blank(lines):\n    return [line for line in lines if line.strip()]\n",
            "def strip_blank(lines):\n    return [line.strip() for line in lines if line.strip()]\n",
        ),
        _task(
            "ce-14",
            "hard",
            "eval",
            "Define merge_spans(spans) merging inclusive [start, end] integer pairs that overlap or touch. Return them sorted.",
            "merge_spans",
            [
                _case([], []),
                _case([[1, 2], [2, 4], [6, 7]], [[1, 4], [6, 7]]),
                _case([[5, 5]], [[5, 5]]),
            ],
            "def merge_spans(spans):\n    ordered = sorted(spans)\n    merged = []\n    for start, end in ordered:\n        if not merged or start > merged[-1][1] + 1:\n            merged.append([start, end])\n        else:\n            merged[-1][1] = max(merged[-1][1], end)\n    return merged\n",
            "def merge_spans(spans):\n    return [list(span) for span in spans]\n",
        ),
        _task(
            "ce-15",
            "medium",
            "eval",
            "Define balanced(text) for (), [], and {}. Ignore other characters. The empty string is balanced.",
            "balanced",
            [
                _case("", True),
                _case("([])", True),
                _case("([)]", False),
                _case("(", False),
                _case("a(b)c", True),
            ],
            "def balanced(text):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    stack = []\n    for char in text:\n        if char in '([{':\n            stack.append(char)\n        elif char in pairs:\n            if not stack or stack.pop() != pairs[char]:\n                return False\n    return not stack\n",
            "def balanced(text):\n    return text.count('(') == text.count(')')\n",
        ),
        _task(
            "ce-16",
            "easy",
            "eval",
            "Define running_max(items). Each position is the greatest value at or before it. The empty list returns [].",
            "running_max",
            [_case([], []), _case([3, 1, 4, 2], [3, 3, 4, 4]), _case([-2, -1], [-2, -1])],
            "def running_max(items):\n    best = None\n    result = []\n    for item in items:\n        best = item if best is None else max(best, item)\n        result.append(best)\n    return result\n",
            "def running_max(items):\n    return [max(items)] * len(items) if items else []\n",
        ),
        _task(
            "ce-17",
            "easy",
            "eval",
            "Define digits_only(text) keeping Unicode decimal digits and dropping other numeric characters such as superscripts.",
            "digits_only",
            [_case("", ""), _case("a1b2", "12"), _case("１２", "１２"), _case("²2", "2")],
            "def digits_only(text):\n    return ''.join(char for char in text if char.isdecimal())\n",
            "def digits_only(text):\n    return ''.join(char for char in text if char.isdigit())\n",
        ),
        _task(
            "ce-18",
            "medium",
            "eval",
            "Define wrap_index(length, index) as index modulo a positive length, including negative indexes.",
            "wrap_index",
            [_case(5, 0, 0), _case(5, 6, 1), _case(5, -1, 4)],
            "def wrap_index(length, index):\n    return index % length\n",
            "def wrap_index(length, index):\n    return index if index >= 0 else 0\n",
        ),
        _task(
            "ce-19",
            "hard",
            "eval",
            "Define title_words(text). Capitalize the first character of each whitespace-separated word and lowercase the rest. Keep apostrophes inside the word.",
            "title_words",
            [_case("", ""), _case("hello WORLD", "Hello World"), _case("o'reilly", "O'reilly")],
            "def title_words(text):\n    return ' '.join(word[:1].upper() + word[1:].lower() for word in text.split())\n",
            "def title_words(text):\n    return text.title()\n",
        ),
        _task(
            "ce-20",
            "hard",
            "eval",
            "Define window_sums(items, width) for each contiguous window. The empty list returns [].",
            "window_sums",
            [_case([], 1, []), _case([1, 2, 3, 4], 2, [3, 5, 7]), _case([5], 1, [5])],
            "def window_sums(items, width):\n    if not items:\n        return []\n    total = sum(items[:width])\n    sums = [total]\n    for index in range(width, len(items)):\n        total += items[index] - items[index - width]\n        sums.append(total)\n    return sums\n",
            "def window_sums(items, width):\n    return [sum(items)] if items else []\n",
        ),
    )
