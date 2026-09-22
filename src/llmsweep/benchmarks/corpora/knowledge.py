"""Curated stable-fact items. Twenty items are deliberately unanswerable."""

# Corpus prompts are data, not wrapped prose.
# ruff: noqa: E501

from __future__ import annotations

from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import TaskSpec

_ANSWERABLE: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("science", "What is the chemical symbol for gold?", ("Au",), "IUPAC"),
    ("science", "What planet is known as the Red Planet?", ("Mars",), "IAU"),
    ("science", "How many planets are in the Solar System?", ("8", "eight"), "IAU"),
    (
        "science",
        "What gas do plants primarily absorb for photosynthesis?",
        ("carbon dioxide", "CO2"),
        "biology",
    ),
    (
        "science",
        "What is the boiling point of water in Celsius at standard pressure?",
        ("100", "100 C"),
        "IUPAC",
    ),
    (
        "science",
        "What particle has a positive charge in an atomic nucleus?",
        ("proton",),
        "physics",
    ),
    (
        "science",
        "What is the hardest natural mineral on the Mohs scale?",
        ("diamond",),
        "mineralogy",
    ),
    ("science", "How many bones are in a typical adult human body?", ("206",), "anatomy"),
    (
        "science",
        "What is the speed of light in vacuum, in km/s, to the nearest thousand?",
        ("300000", "300,000"),
        "CODATA",
    ),
    (
        "science",
        "Which blood type is the universal donor for red cells?",
        ("O negative", "O-"),
        "transfusion",
    ),
    ("science", "What organ produces insulin?", ("pancreas",), "anatomy"),
    (
        "science",
        "What is the powerhouse organelle of a eukaryotic cell?",
        ("mitochondrion", "mitochondria"),
        "biology",
    ),
    ("science", "Which element has atomic number 1?", ("hydrogen",), "IUPAC"),
    ("science", "What is H2O commonly called?", ("water",), "chemistry"),
    ("science", "How many chromosomes are in a typical human somatic cell?", ("46",), "genetics"),
    ("science", "What force keeps planets in orbit around the Sun?", ("gravity",), "physics"),
    ("science", "Which metal is liquid at room temperature?", ("mercury",), "chemistry"),
    ("science", "What is the largest planet in the Solar System?", ("Jupiter",), "IAU"),
    ("science", "What tissue connects muscle to bone?", ("tendon",), "anatomy"),
    ("science", "What is the freezing point of water in Celsius?", ("0", "0 C"), "IUPAC"),
    ("history", "In what year did World War II end?", ("1945",), "historical record"),
    (
        "history",
        "Who was the first President of the United States?",
        ("George Washington",),
        "US history",
    ),
    ("history", "In what year did the Berlin Wall fall?", ("1989",), "historical record"),
    ("history", "Which ancient civilization built Machu Picchu?", ("Inca", "the Inca"), "history"),
    (
        "history",
        "Who wrote the play Romeo and Juliet?",
        ("William Shakespeare", "Shakespeare"),
        "literature",
    ),
    ("history", "In what year did the Apollo 11 Moon landing occur?", ("1969",), "NASA"),
    (
        "history",
        "Which country gifted the Statue of Liberty to the United States?",
        ("France",),
        "history",
    ),
    (
        "history",
        "Who was the British prime minister for most of World War II?",
        ("Winston Churchill", "Churchill"),
        "history",
    ),
    (
        "history",
        "In what century did the printing press attributed to Gutenberg appear?",
        ("15th", "fifteenth"),
        "history",
    ),
    ("history", "Which empire built the Colosseum?", ("Roman", "the Roman Empire"), "history"),
    (
        "history",
        "In what year was the United States Declaration of Independence adopted?",
        ("1776",),
        "US history",
    ),
    ("history", "Who painted the Mona Lisa?", ("Leonardo da Vinci", "Leonardo"), "art history"),
    (
        "history",
        "Which war was fought between the north and south of the United States from 1861 to 1865?",
        ("the American Civil War", "American Civil War", "Civil War"),
        "US history",
    ),
    ("history", "In what year did the Titanic sink?", ("1912",), "historical record"),
    ("history", "Who was the first person to walk on the Moon?", ("Neil Armstrong",), "NASA"),
    (
        "history",
        "Which city was the capital of the Byzantine Empire?",
        ("Constantinople",),
        "history",
    ),
    ("history", "In what year did the French Revolution begin?", ("1789",), "history"),
    (
        "history",
        "Who composed the four violin concertos known as The Four Seasons?",
        ("Antonio Vivaldi", "Vivaldi"),
        "music history",
    ),
    (
        "history",
        "Which document begins We the People in the United States?",
        ("the Constitution", "US Constitution", "Constitution"),
        "US history",
    ),
    (
        "history",
        "In what year did Christopher Columbus first reach the Americas?",
        ("1492",),
        "history",
    ),
    ("geography", "What is the capital of Japan?", ("Tokyo",), "gazetteer"),
    ("geography", "What is the capital of Canada?", ("Ottawa",), "gazetteer"),
    ("geography", "Which river is the longest in Africa?", ("the Nile", "Nile"), "gazetteer"),
    (
        "geography",
        "What is the largest ocean on Earth?",
        ("Pacific", "the Pacific Ocean", "Pacific Ocean"),
        "gazetteer",
    ),
    ("geography", "On which continent is the Sahara Desert?", ("Africa",), "gazetteer"),
    ("geography", "What is the capital of Australia?", ("Canberra",), "gazetteer"),
    (
        "geography",
        "Which mountain is the highest above sea level?",
        ("Mount Everest", "Everest"),
        "survey",
    ),
    ("geography", "What is the capital of Egypt?", ("Cairo",), "gazetteer"),
    ("geography", "Which US state is an archipelago?", ("Hawaii",), "US geography"),
    ("geography", "What is the capital of France?", ("Paris",), "gazetteer"),
    (
        "geography",
        "Which desert covers much of Mongolia and northern China?",
        ("Gobi", "the Gobi"),
        "gazetteer",
    ),
    ("geography", "What is the capital of Italy?", ("Rome",), "gazetteer"),
    ("geography", "Which continent is also a country?", ("Australia",), "gazetteer"),
    ("geography", "What is the capital of Kenya?", ("Nairobi",), "gazetteer"),
    (
        "geography",
        "Which ocean lies between Africa and Australia?",
        ("Indian", "the Indian Ocean", "Indian Ocean"),
        "gazetteer",
    ),
    ("geography", "What is the capital of Spain?", ("Madrid",), "gazetteer"),
    (
        "geography",
        "Which Great Lake is entirely within the United States?",
        ("Lake Michigan", "Michigan"),
        "US geography",
    ),
    ("geography", "What is the capital of Norway?", ("Oslo",), "gazetteer"),
    ("geography", "Which country has the city of Marrakesh?", ("Morocco",), "gazetteer"),
    ("geography", "What is the capital of South Korea?", ("Seoul",), "gazetteer"),
    (
        "technology",
        "Who is credited with inventing the World Wide Web?",
        ("Tim Berners-Lee",),
        "W3C history",
    ),
    ("technology", "What does CPU stand for?", ("central processing unit",), "computing"),
    ("technology", "What numeric base does binary use?", ("2", "two"), "computing"),
    (
        "technology",
        "Who originally created the Python programming language?",
        ("Guido van Rossum",),
        "Python history",
    ),
    (
        "technology",
        "What does HTML stand for?",
        ("HyperText Markup Language", "hypertext markup language"),
        "W3C",
    ),
    ("technology", "How many bits are in a byte?", ("8", "eight"), "computing"),
    ("technology", "What protocol commonly delivers web pages?", ("HTTP", "HTTPS"), "IETF"),
    ("technology", "What does RAM stand for?", ("random access memory",), "computing"),
    (
        "technology",
        "What number base is the binary numeral system?",
        ("2", "two", "base 2"),
        "computing",
    ),
    ("technology", "What does URL stand for?", ("uniform resource locator",), "IETF"),
    (
        "technology",
        "Which language uses indentation to delimit blocks in its usual style and was created by Guido van Rossum?",
        ("Python",),
        "Python history",
    ),
    ("technology", "What does SQL stand for?", ("structured query language",), "databases"),
    ("technology", "How many hexadecimal digits represent one byte?", ("2", "two"), "computing"),
    ("technology", "What does DNS stand for?", ("domain name system",), "IETF"),
    (
        "technology",
        "Which numeric value is the Boolean false in Python when converted with int?",
        ("0", "zero"),
        "Python",
    ),
    ("technology", "What does API stand for?", ("application programming interface",), "computing"),
    (
        "technology",
        "Which company maintains the CPython project on GitHub under the python organization?",
        ("the Python Software Foundation", "PSF", "Python Software Foundation"),
        "PSF",
    ),
    ("technology", "What does GPU stand for?", ("graphics processing unit",), "computing"),
    ("technology", "How many bits are in two bytes?", ("16", "sixteen"), "computing"),
    (
        "technology",
        "What does IDE stand for in software development?",
        ("integrated development environment",),
        "computing",
    ),
)
_ABSTAIN: tuple[tuple[str, str], ...] = (
    ("What is the exact population of Paris at this second?", "changes continuously"),
    ("Which unpublished private key is on this machine?", "not a public fact"),
    ("What will the closing price of AAPL be tomorrow?", "future market price"),
    ("Who will win the next World Cup?", "future event"),
    ("What is the user's mother's maiden name?", "private personal data"),
    ("How many grains of sand are on Earth right now?", "not a stable measurable fact"),
    ("What is the password for this benchmark?", "secret"),
    ("Which stock should a reader buy today?", "advice, not a fact"),
    ("What did I eat for breakfast?", "unknown private event"),
    ("What is the current temperature outside this computer?", "unspecified location and time"),
    ("Who is the best musician of all time?", "subjective"),
    ("What is the meaning of an unspecified 'it'?", "underspecified"),
    ("How many people are inside an unnamed building?", "underspecified"),
    ("What color is the car?", "no car was identified"),
    ("When will the next magnitude 9 earthquake occur?", "future event"),
    ("What is the serial number of this laptop?", "private device data"),
    ("Which answer did the previous hidden test expect?", "not part of the question"),
    ("What is the weather in an unspecified city tomorrow?", "underspecified future"),
    ("Who is the current monarch of a country that has no monarch?", "false premise"),
    ("What is  the exact number of fish in the ocean today?", "not a stable count"),
)


def knowledge_tasks() -> tuple[TaskSpec, ...]:
    specs: list[TaskSpec] = []
    for index, (category, question, aliases, provenance) in enumerate(_ANSWERABLE, 1):
        specs.append(_item(index, category, question, aliases, False, provenance))
    for offset, (question, provenance) in enumerate(_ABSTAIN, 1):
        specs.append(_item(80 + offset, "unanswerable", question, (), True, provenance))
    return tuple(specs)


def _item(
    index: int,
    category: str,
    question: str,
    aliases: tuple[str, ...],
    abstain: bool,
    provenance: str,
) -> TaskSpec:
    task_id = f"k-{index:03d}"
    prompt = (
        'Reply with JSON only: {"answer": string, "confidence": number from 0 to 1, '
        '"abstain": boolean}. Abstain when the question cannot be answered as a stable fact. '
        "Question: " + question
    )
    payload = {
        "question": question,
        "aliases": list(aliases),
        "abstain": abstain,
        "provenance": provenance,
        "license": "facts compiled for llmsweep; short factual items",
        "review": "2026-09-22",
    }
    return TaskSpec(
        pack_id="builtin",
        pack_version="1",
        suite_id="knowledge-cal",
        task_id=task_id,
        prompt=prompt,
        evaluator_id="knowledge-cal",
        evaluator_version="1",
        category=category,
        difficulty="easy" if not abstain else "medium",
        partition="dev" if index <= 4 or (abstain and index > 96) else "eval",
        requires_tools=False,
        execution="workflow",
        max_turns=1,
        max_tokens=512,
        task_seconds=60,
        content_digest=fingerprint(
            {"prompt": prompt, "payload": payload, "evaluator": "knowledge-cal/1"}
        ),
        single_turn=True,
        payload=payload,
    )
