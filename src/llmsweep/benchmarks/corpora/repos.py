"""Small repository tasks for multi-file changes and defect repair."""

# Fixture source and prompts are data, not wrapped prose.
# ruff: noqa: E501

from __future__ import annotations

from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import TaskSpec


def _spec(
    suite: str,
    task_id: str,
    prompt: str,
    files: dict[str, str],
    writable: list[str],
    protected: list[str],
    public_test: str,
    hidden_test: str,
    reference_files: dict[str, str],
) -> TaskSpec:
    payload = {
        "files": files,
        "writable": writable,
        "protected": protected,
        "public_test": public_test,
        "hidden_test": hidden_test,
        "reference_files": reference_files,
    }
    return TaskSpec(
        pack_id="builtin",
        pack_version="1",
        suite_id=suite,
        task_id=task_id,
        prompt=prompt,
        evaluator_id=suite,
        evaluator_version="1",
        category="agentic-coding",
        difficulty="medium",
        partition="eval",
        requires_tools=True,
        execution="isolated",
        max_turns=20,
        max_tokens=4096,
        task_seconds=300,
        content_digest=fingerprint(
            {"payload": payload, "prompt": prompt, "evaluator": suite + "/1"}
        ),
        tools=("list_files", "read_file", "grep", "write_file", "run_public_tests"),
        payload=payload,
    )


def _feature(
    task_id: str,
    prompt: str,
    files: dict[str, str],
    writable: list[str],
    public_test: str,
    hidden_test: str,
    reference_files: dict[str, str],
) -> TaskSpec:
    protected = [name for name in files if name not in writable]
    return _spec(
        "multi-file",
        task_id,
        prompt,
        files,
        writable,
        protected,
        public_test,
        hidden_test,
        reference_files,
    )


def _issue(
    task_id: str,
    prompt: str,
    files: dict[str, str],
    writable: list[str],
    public_test: str,
    hidden_test: str,
    reference_files: dict[str, str],
) -> TaskSpec:
    protected = [name for name in files if name not in writable]
    return _spec(
        "repo-issue",
        task_id,
        prompt,
        files,
        writable,
        protected,
        public_test,
        hidden_test,
        reference_files,
    )


def multi_file_tasks() -> tuple[TaskSpec, ...]:
    return (
        _feature(
            "mf-01",
            "Add a CLI flag --upper that makes greet(name) return an uppercase greeting. Preserve the default greeting.",
            {
                "app.py": "def greet(name, upper=False):\n    text = f'hello {name}'\n    return text\n",
                "cli.py": "from app import greet\n\ndef main(argv):\n    name = argv[1] if len(argv) > 1 else 'world'\n    return greet(name)\n",
                "README.md": "greet returns hello plus the name.\n",
            },
            ["app.py", "cli.py"],
            "from cli import main\nassert main(['prog', 'sam']) == 'hello sam'\nprint('public ok')\n",
            "from cli import main\nassert main(['prog', '--upper', 'sam']) == 'HELLO SAM'\nassert main(['prog', 'sam']) == 'hello sam'\nprint('all tests passed')\n",
            {
                "app.py": "def greet(name, upper=False):\n    text = f'hello {name}'\n    return text.upper() if upper else text\n",
                "cli.py": "from app import greet\n\ndef main(argv):\n    upper = '--upper' in argv\n    name = 'world'\n    for arg in argv[1:]:\n        if arg != '--upper':\n            name = arg\n    return greet(name, upper)\n",
            },
        ),
        _feature(
            "mf-02",
            "Extend the parser so lines starting with # are comments and blank lines are skipped.",
            {
                "parser.py": "def parse(text):\n    return [line.strip() for line in text.splitlines()]\n",
                "tokens.py": "def nonempty(rows):\n    return [row for row in rows if row]\n",
                "README.md": "parse returns stripped lines.\n",
            },
            ["parser.py"],
            "from parser import parse\nassert parse('a\\n\\nb') == ['a', '', 'b'] or parse('a\\n\\nb') == ['a', 'b']\nprint('public ok')\n",
            "from parser import parse\nassert parse('# c\\na\\n\\nb') == ['a', 'b']\nassert parse('') == []\nprint('all tests passed')\n",
            {
                "parser.py": "def parse(text):\n    rows = []\n    for line in text.splitlines():\n        stripped = line.strip()\n        if not stripped or stripped.startswith('#'):\n            continue\n        rows.append(stripped)\n    return rows\n"
            },
        ),
        _feature(
            "mf-03",
            "Reject configuration values that are not positive integers. Keep load_config reading key=value lines.",
            {
                "config.py": "def load_config(text):\n    data = {}\n    for line in text.splitlines():\n        if not line.strip():\n            continue\n        key, value = line.split('=', 1)\n        data[key.strip()] = int(value)\n    return data\n",
                "validate.py": "def check(data):\n    return data\n",
                "README.md": "config values are integers.\n",
            },
            ["config.py", "validate.py"],
            "from config import load_config\nassert load_config('limit=2')['limit'] == 2\nprint('public ok')\n",
            "from config import load_config\ntry:\n    load_config('limit=0')\nexcept ValueError:\n    pass\nelse:\n    raise SystemExit('zero accepted')\nassert load_config('limit=2')['limit'] == 2\nprint('all tests passed')\n",
            {
                "config.py": "def load_config(text):\n    data = {}\n    for line in text.splitlines():\n        if not line.strip():\n            continue\n        key, value = line.split('=', 1)\n        number = int(value)\n        if number <= 0:\n            raise ValueError(key)\n        data[key.strip()] = number\n    return data\n"
            },
        ),
        _feature(
            "mf-04",
            "Propagate the prefix from settings into render(). Existing unprefixed behavior must remain when prefix is empty.",
            {
                "settings.py": "PREFIX = ''\n",
                "render.py": "def render(text):\n    return text\n",
                "app.py": "from render import render\n\ndef show(text):\n    return render(text)\n",
                "README.md": "render returns the text.\n",
            },
            ["render.py", "settings.py"],
            "import settings\nsettings.PREFIX = ''\nfrom app import show\nassert show('x') == 'x'\nprint('public ok')\n",
            "import settings\nsettings.PREFIX = 'id:'\nfrom importlib import reload\nimport render, app\nreload(render)\nreload(app)\nassert app.show('x') == 'id:x'\nprint('all tests passed')\n",
            {
                "render.py": "import settings\n\ndef render(text):\n    return f'{settings.PREFIX}{text}'\n"
            },
        ),
        _feature(
            "mf-05",
            "Serialize points as 'x,y' and parse that form back. Do not change Point's fields.",
            {
                "point.py": "class Point:\n    def __init__(self, x, y):\n        self.x = x\n        self.y = y\n",
                "codec.py": "from point import Point\n\ndef dump(point):\n    return str(point.x)\n\ndef load(text):\n    return Point(int(text), 0)\n",
                "README.md": "points have x and y.\n",
            },
            ["codec.py"],
            "from point import Point\nfrom codec import dump, load\nassert dump(Point(1, 2)).startswith('1')\nprint('public ok')\n",
            "from point import Point\nfrom codec import dump, load\nassert dump(Point(1, 2)) == '1,2'\nloaded = load('3,4')\nassert (loaded.x, loaded.y) == (3, 4)\nprint('all tests passed')\n",
            {
                "codec.py": "from point import Point\n\ndef dump(point):\n    return f'{point.x},{point.y}'\n\ndef load(text):\n    x, y = text.split(',')\n    return Point(int(x), int(y))\n"
            },
        ),
        _feature(
            "mf-06",
            "Integrate tax.rate into checkout.total. A missing rate means zero tax. Leave the catalog prices unchanged.",
            {
                "catalog.py": "PRICES = {'pen': 10, 'book': 20}\n",
                "tax.py": "rate = 0\n",
                "checkout.py": "from catalog import PRICES\n\ndef total(items):\n    return sum(PRICES[item] for item in items)\n",
                "README.md": "total sums catalog prices.\n",
            },
            ["checkout.py"],
            "from checkout import total\nassert total(['pen']) == 10\nprint('public ok')\n",
            "import tax\ntax.rate = 0.1\nfrom importlib import reload\nimport checkout\nreload(checkout)\nassert checkout.total(['pen', 'book']) == 33\nprint('all tests passed')\n",
            {
                "checkout.py": "from catalog import PRICES\nimport tax\n\ndef total(items):\n    amount = sum(PRICES[item] for item in items)\n    return amount + amount * tax.rate\n"
            },
        ),
    )


def repo_issue_tasks() -> tuple[TaskSpec, ...]:
    return (
        _issue(
            "ri-01",
            "total(items) skips the first item. Fix the defect and keep the empty-list result at 0.",
            {
                "stats.py": "def total(items):\n    value = 0\n    for index in range(1, len(items)):\n        value += items[index]\n    return value\n",
                "tests_public.py": "from stats import total\nassert total([1, 2, 3]) == 5\nprint('public reproduces the defect')\n",
                "README.md": "total should sum every item.\n",
            },
            ["stats.py"],
            "from stats import total\nprint(total([1, 2, 3]))\n",
            "from stats import total\nassert total([1, 2, 3]) == 6\nassert total([]) == 0\nassert total([5]) == 5\nprint('all tests passed')\n",
            {"stats.py": "def total(items):\n    return sum(items)\n"},
        ),
        _issue(
            "ri-02",
            "normalize() should strip surrounding whitespace and casefold. It currently only strips.",
            {
                "textutil.py": "def normalize(text):\n    return text.strip()\n",
                "tests_public.py": "from textutil import normalize\nassert normalize(' A ') == 'A'\n",
                "README.md": "normalize prepares text for comparison.\n",
            },
            ["textutil.py"],
            "from textutil import normalize\nassert normalize(' A ') == 'A'\nprint('public ok')\n",
            "from textutil import normalize\nassert normalize(' A ') == 'a'\nassert normalize('É') == 'é'\nprint('all tests passed')\n",
            {"textutil.py": "def normalize(text):\n    return text.strip().casefold()\n"},
        ),
        _issue(
            "ri-03",
            "The stack pop on an empty stack should raise IndexError. It currently returns None.",
            {
                "stack.py": "class Stack:\n    def __init__(self):\n        self._items = []\n    def push(self, item):\n        self._items.append(item)\n    def pop(self):\n        if not self._items:\n            return None\n        return self._items.pop()\n",
                "README.md": "Stack is a LIFO list.\n",
            },
            ["stack.py"],
            "from stack import Stack\nstack = Stack()\nstack.push(1)\nassert stack.pop() == 1\nprint('public ok')\n",
            "from stack import Stack\nstack = Stack()\ntry:\n    stack.pop()\nexcept IndexError:\n    pass\nelse:\n    raise SystemExit('missing IndexError')\nstack.push(1)\nassert stack.pop() == 1\nprint('all tests passed')\n",
            {
                "stack.py": "class Stack:\n    def __init__(self):\n        self._items = []\n    def push(self, item):\n        self._items.append(item)\n    def pop(self):\n        if not self._items:\n            raise IndexError('pop from empty stack')\n        return self._items.pop()\n"
            },
        ),
        _issue(
            "ri-04",
            "slice_page(items, page, size) uses a 1-based page but currently starts at zero. Fix paging without changing size handling.",
            {
                "pages.py": "def slice_page(items, page, size):\n    start = page * size\n    return list(items[start:start + size])\n",
                "README.md": "page 1 is the first page.\n",
            },
            ["pages.py"],
            "from pages import slice_page\nassert slice_page([1, 2, 3, 4], 0, 2) == [1, 2]\nprint('public shows zero-based behavior')\n",
            "from pages import slice_page\nassert slice_page([1, 2, 3, 4], 1, 2) == [1, 2]\nassert slice_page([1, 2, 3, 4], 2, 2) == [3, 4]\nprint('all tests passed')\n",
            {
                "pages.py": "def slice_page(items, page, size):\n    start = (page - 1) * size\n    return list(items[start:start + size])\n"
            },
        ),
        _issue(
            "ri-05",
            "find() should return -1 when the value is missing. It currently raises ValueError.",
            {
                "search.py": "def find(items, value):\n    return items.index(value)\n",
                "README.md": "find returns the first index.\n",
            },
            ["search.py"],
            "from search import find\nassert find([1, 2], 2) == 1\nprint('public ok')\n",
            "from search import find\nassert find([1, 2], 3) == -1\nassert find([1, 2], 1) == 0\nprint('all tests passed')\n",
            {
                "search.py": "def find(items, value):\n    try:\n        return items.index(value)\n    except ValueError:\n        return -1\n"
            },
        ),
        _issue(
            "ri-06",
            "Counter.add should ignore None values and still count the others. It currently crashes on None.",
            {
                "counter.py": "class Counter:\n    def __init__(self):\n        self.value = 0\n    def add(self, items):\n        for item in items:\n            self.value += item\n        return self.value\n",
                "README.md": "Counter sums numbers.\n",
            },
            ["counter.py"],
            "from counter import Counter\nassert Counter().add([1, 2]) == 3\nprint('public ok')\n",
            "from counter import Counter\nassert Counter().add([1, None, 2]) == 3\nassert Counter().add([]) == 0\nprint('all tests passed')\n",
            {
                "counter.py": "class Counter:\n    def __init__(self):\n        self.value = 0\n    def add(self, items):\n        for item in items:\n            if item is None:\n                continue\n            self.value += item\n        return self.value\n"
            },
        ),
        _issue(
            "ri-07",
            "join_url should not duplicate slashes when the base already ends with a slash.",
            {
                "urls.py": "def join_url(base, path):\n    return base + '/' + path\n",
                "README.md": "join_url concatenates a base and a path.\n",
            },
            ["urls.py"],
            "from urls import join_url\nassert 'a' in join_url('http://ex', 'a')\nprint('public ok')\n",
            "from urls import join_url\nassert join_url('http://ex/', 'a') == 'http://ex/a'\nassert join_url('http://ex', 'a') == 'http://ex/a'\nprint('all tests passed')\n",
            {
                "urls.py": "def join_url(base, path):\n    return base.rstrip('/') + '/' + path.lstrip('/')\n"
            },
        ),
        _issue(
            "ri-08",
            "average() of an empty list should return None. It currently raises ZeroDivisionError.",
            {
                "maths.py": "def average(items):\n    return sum(items) / len(items)\n",
                "README.md": "average returns the arithmetic mean.\n",
            },
            ["maths.py"],
            "from maths import average\nassert average([2, 4]) == 3\nprint('public ok')\n",
            "from maths import average\nassert average([]) is None\nassert average([2, 4]) == 3\nprint('all tests passed')\n",
            {
                "maths.py": "def average(items):\n    if not items:\n        return None\n    return sum(items) / len(items)\n"
            },
        ),
    )
