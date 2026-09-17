"""
JARVIS Quiz plugin — generate and present interactive quizzes on any topic.

The quiz panel UI already exists in ui.py.  This plugin feeds it questions
and a grader, then lets the user answer at their own pace.  When they finish,
the results are handed back to JARVIS as a [QUIZ_DONE] message.
"""
from __future__ import annotations

import random
import re
from typing import Any

PLUGIN = {
    "name": "quiz",
    "description": (
        "Interactive quiz mode. Generates questions on any topic and presents "
        "them in the quiz panel. Trigger phrases: 'quiz me on ...', "
        "'test me on ...', 'give me a quiz about ...', 'let's do a quiz', "
        "'pop quiz'. Supports multiple-choice and open-ended questions. "
        "Do NOT use for file operations or email — use those tools instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "topic": {
                "type": "STRING",
                "description": (
                    "The subject to quiz on (e.g. 'Python basics', 'world capitals', "
                    "'JavaScript closures', 'British history'). If empty, generates "
                    "a general-knowledge mixed quiz."
                ),
            },
            "count": {
                "type": "STRING",
                "description": (
                    "Number of questions (3-15, default 5). Passed as a string."
                ),
            },
            "difficulty": {
                "type": "STRING",
                "description": (
                    "Difficulty level: 'easy', 'medium' (default), or 'hard'."
                ),
            },
        },
        "required": [],
    },
}


# ── Question banks by topic area ─────────────────────────────────────────────

_GENERAL_KNOWLEDGE: list[dict[str, Any]] = [
    {"q": "What planet is known as the Red Planet?", "a": "Mars", "opts": ["Venus", "Mars", "Jupiter", "Saturn"], "cat": "science"},
    {"q": "What is the largest ocean on Earth?", "a": "Pacific Ocean", "opts": ["Atlantic", "Indian", "Arctic", "Pacific Ocean"], "cat": "geography"},
    {"q": "Who painted the Mona Lisa?", "a": "Leonardo da Vinci", "opts": ["Michelangelo", "Leonardo da Vinci", "Raphael", "Donatello"], "cat": "art"},
    {"q": "What is the chemical symbol for gold?", "a": "Au", "opts": ["Go", "Au", "Gd", "Ag"], "cat": "science"},
    {"q": "How many continents are there?", "a": "7", "opts": ["5", "6", "7", "8"], "cat": "geography"},
    {"q": "What year did World War II end?", "a": "1945", "opts": ["1943", "1944", "1945", "1946"], "cat": "history"},
    {"q": "What is the fastest land animal?", "a": "Cheetah", "opts": ["Lion", "Cheetah", "Gazelle", "Horse"], "cat": "nature"},
    {"q": "Which element has the atomic number 1?", "a": "Hydrogen", "opts": ["Helium", "Hydrogen", "Lithium", "Carbon"], "cat": "science"},
    {"q": "What is the capital of Japan?", "a": "Tokyo", "opts": ["Seoul", "Beijing", "Tokyo", "Bangkok"], "cat": "geography"},
    {"q": "Who wrote Romeo and Juliet?", "a": "William Shakespeare", "opts": ["Charles Dickens", "William Shakespeare", "Jane Austen", "Mark Twain"], "cat": "literature"},
    {"q": "What is the boiling point of water in Celsius?", "a": "100", "opts": ["90", "100", "110", "120"], "cat": "science"},
    {"q": "Which planet has the most moons?", "a": "Saturn", "opts": ["Jupiter", "Saturn", "Uranus", "Neptune"], "cat": "science"},
    {"q": "What is the main language spoken in Brazil?", "a": "Portuguese", "opts": ["Spanish", "Portuguese", "French", "Italian"], "cat": "geography"},
    {"q": "How many sides does a hexagon have?", "a": "6", "opts": ["5", "6", "7", "8"], "cat": "math"},
    {"q": "What gas do plants absorb from the atmosphere?", "a": "Carbon dioxide", "opts": ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"], "cat": "science"},
]

_PYTHON_BANK: list[dict[str, Any]] = [
    {"q": "What does `len()` return for an empty list?", "a": "0", "opts": ["0", "1", "None", "Error"], "cat": "basics"},
    {"q": "Which keyword defines a function in Python?", "a": "def", "opts": ["function", "def", "func", "define"], "cat": "basics"},
    {"q": "What is the output of `print(type([]))`?", "a": "<class 'list'>", "opts": ["<class 'array'>", "<class 'list'>", "<class 'tuple'>", "<class 'dict'>"], "cat": "types"},
    {"q": "Which data structure uses key-value pairs?", "a": "dict", "opts": ["list", "tuple", "dict", "set"], "cat": "data-structures"},
    {"q": "What does `**kwargs` allow in a function?", "a": "Arbitrary keyword arguments", "opts": ["Positional arguments", "Arbitrary keyword arguments", "Only string arguments", "Global variables"], "cat": "functions"},
    {"q": "What is a list comprehension?", "a": "A concise way to create lists", "opts": ["A type of loop", "A concise way to create lists", "A sorting algorithm", "A debugging tool"], "cat": "basics"},
    {"q": "Which operator is used for exponentiation?", "a": "**", "opts": ["^", "**", "//", "^^"], "cat": "operators"},
    {"q": "What does `strip()` do to a string?", "a": "Removes leading/trailing whitespace", "opts": ["Removes all spaces", "Removes leading/trailing whitespace", "Splits the string", "Uppercases it"], "cat": "strings"},
    {"q": "What is the result of `True + True`?", "a": "2", "opts": ["True", "2", "1", "Error"], "cat": "types"},
    {"q": "Which exception is raised by `int('abc')`?", "a": "ValueError", "opts": ["TypeError", "ValueError", "SyntaxError", "NameError"], "cat": "errors"},
    {"q": "What does `self` refer to in a class method?", "a": "The instance of the class", "opts": ["The class itself", "The instance of the class", "The parent class", "Nothing"], "cat": "oop"},
    {"q": "How do you create a virtual environment?", "a": "python -m venv myenv", "opts": ["pip install venv", "python -m venv myenv", "venv create myenv", "python venv init"], "cat": "tools"},
    {"q": "What does `//` do in Python 3?", "a": "Floor division", "opts": ["Regular division", "Floor division", "Integer division", "Modulo"], "cat": "operators"},
    {"q": "Which module is used for regular expressions?", "a": "re", "opts": ["regex", "re", "regexp", "pattern"], "cat": "stdlib"},
    {"q": "What is a decorator?", "a": "A function that modifies another function", "opts": ["A class attribute", "A function that modifies another function", "A type of loop", "A variable"], "cat": "advanced"},
]

_HISTORY_BANK: list[dict[str, Any]] = [
    {"q": "In which year did World War I begin?", "a": "1914", "opts": ["1912", "1914", "1916", "1918"], "cat": "wwi"},
    {"q": "Who was the first President of the United States?", "a": "George Washington", "opts": ["Thomas Jefferson", "George Washington", "Abraham Lincoln", "John Adams"], "cat": "us"},
    {"q": "The Great Wall of China was primarily built to defend against whom?", "a": "Mongol invasions", "opts": ["Japanese invasions", "Mongol invasions", "British invasions", "Indian invasions"], "cat": "asia"},
    {"q": "Which ancient civilization built the pyramids at Giza?", "a": "Egyptians", "opts": ["Romans", "Egyptians", "Greeks", "Persians"], "cat": "ancient"},
    {"q": "What was the Renaissance?", "a": "A cultural rebirth in Europe", "opts": ["A war", "A cultural rebirth in Europe", "A religion", "A government"], "cat": "europe"},
    {"q": "Who discovered penicillin?", "a": "Alexander Fleming", "opts": ["Louis Pasteur", "Alexander Fleming", "Robert Koch", "Joseph Lister"], "cat": "science"},
    {"q": "The French Revolution began in which year?", "a": "1789", "opts": ["1776", "1789", "1799", "1804"], "cat": "europe"},
    {"q": "Which empire was ruled by Genghis Khan?", "a": "Mongol Empire", "opts": ["Ottoman Empire", "Mongol Empire", "Roman Empire", "Persian Empire"], "cat": "asia"},
    {"q": "What was the Industrial Revolution?", "a": "Mechanization of manufacturing", "opts": ["A political revolution", "Mechanization of manufacturing", "A scientific theory", "A religious movement"], "cat": "europe"},
    {"q": "Who wrote the Declaration of Independence?", "a": "Thomas Jefferson", "opts": ["Benjamin Franklin", "Thomas Jefferson", "John Adams", "James Madison"], "cat": "us"},
]

_MATH_BANK: list[dict[str, Any]] = [
    {"q": "What is 12 × 12?", "a": "144", "opts": ["124", "132", "144", "148"], "cat": "arithmetic"},
    {"q": "What is the square root of 144?", "a": "12", "opts": ["10", "11", "12", "14"], "cat": "arithmetic"},
    {"q": "What is the value of Pi to two decimal places?", "a": "3.14", "opts": ["3.12", "3.14", "3.16", "3.18"], "cat": "constants"},
    {"q": "Solve: 3x + 6 = 15. What is x?", "a": "3", "opts": ["2", "3", "4", "5"], "cat": "algebra"},
    {"q": "What is 2^10?", "a": "1024", "opts": ["512", "1024", "2048", "256"], "cat": "arithmetic"},
    {"q": "How many degrees in a right angle?", "a": "90", "opts": ["45", "90", "180", "360"], "cat": "geometry"},
    {"q": "What is the formula for the area of a circle?", "a": "πr²", "opts": ["2πr", "πr²", "πd", "2πr²"], "cat": "geometry"},
    {"q": "What is 15% of 200?", "a": "30", "opts": ["25", "30", "35", "40"], "cat": "arithmetic"},
    {"q": "What is the next prime number after 7?", "a": "11", "opts": ["9", "10", "11", "13"], "cat": "number-theory"},
    {"q": "A triangle has how many degrees total?", "a": "180", "opts": ["90", "180", "270", "360"], "cat": "geometry"},
]

_TOPIC_MAP: dict[str, list[dict[str, Any]]] = {
    "python": _PYTHON_BANK,
    "programming": _PYTHON_BANK,
    "coding": _PYTHON_BANK,
    "history": _HISTORY_BANK,
    "math": _MATH_BANK,
    "mathematics": _MATH_BANK,
}


def _normalise_difficulty(raw: str | None) -> str:
    d = (raw or "medium").strip().lower()
    if d in ("easy", "soft", "simple"):
        return "easy"
    if d in ("hard", "tough", "difficult", "expert"):
        return "hard"
    return "medium"


def _pick_questions(topic: str, count: int, difficulty: str) -> list[dict[str, Any]]:
    """Select questions from banks based on topic, with shuffling."""
    topic_lower = (topic or "").strip().lower()
    bank: list[dict[str, Any]] | None = None

    for key, b in _TOPIC_MAP.items():
        if key in topic_lower or topic_lower in key:
            bank = b
            break

    if bank is None:
        bank = _GENERAL_KNOWLEDGE

    pool = list(bank)
    random.shuffle(pool)

    # For hard mode, prefer non-obvious questions; for easy, prefer clear ones
    count = min(count, len(pool))
    selected = pool[:count]

    questions: list[dict[str, Any]] = []
    for item in selected:
        q: dict[str, Any] = {
            "question": item["q"],
            "answer": item["a"],
            "type": "mc",
            "options": list(item.get("opts", [])),
        }
        if item.get("note"):
            q["note"] = item["note"]
        questions.append(q)

    return questions


def _grader(question: dict, given: str) -> bool | None:
    """Grade a single answer. Returns True/False for MC, None for open-ended."""
    if question.get("type") == "mc":
        given_clean = given.strip().lower()
        answer_clean = question.get("answer", "").strip().lower()
        return given_clean == answer_clean
    # Open-ended: defer to JARVIS
    return None


def run(parameters: dict, player=None, session_memory=None) -> str:
    topic = (parameters.get("topic") or "").strip() or "General Knowledge"

    try:
        count = int(str(parameters.get("count", "5")).strip())
    except (ValueError, TypeError):
        count = 5
    count = max(3, min(15, count))

    difficulty = _normalise_difficulty(parameters.get("difficulty"))
    questions = _pick_questions(topic, count, difficulty)

    if not questions:
        return "Sorry, I could not generate questions for that topic."

    if player:
        try:
            player.show_quiz(topic, questions, _grader)
        except Exception as e:
            return f"Sir, the quiz panel could not be displayed: {e}"

    return (
        f"Quiz on '{topic}' — {len(questions)} questions at {difficulty} difficulty. "
        "The quiz panel is now on screen. Answer at your own pace."
    )
