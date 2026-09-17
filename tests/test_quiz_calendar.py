"""
JARVIS Quiz & Calendar Plugin Test Suite.

Tests plugins/quiz.py and plugins/calendar.py.
"""
import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Quiz Plugin Tests ────────────────────────────────────────────────────────

class TestQuizPluginStructure(unittest.TestCase):
    """Verify the quiz plugin has the correct loader contract."""

    def setUp(self):
        from plugins import quiz
        self.mod = quiz

    def test_plugin_dict_exists(self):
        self.assertIsInstance(self.mod.PLUGIN, dict)

    def test_plugin_name(self):
        self.assertEqual(self.mod.PLUGIN["name"], "quiz")

    def test_plugin_description(self):
        self.assertTrue(len(self.mod.PLUGIN["description"]) > 20)

    def test_plugin_parameters_type(self):
        self.assertEqual(self.mod.PLUGIN["parameters"]["type"], "OBJECT")

    def test_run_callable(self):
        self.assertTrue(callable(self.mod.run))


class TestQuizQuestionSelection(unittest.TestCase):
    """Test question selection and topic matching."""

    def _pick(self, topic, count, difficulty):
        from plugins.quiz import _pick_questions
        return _pick_questions(topic, count, difficulty)

    def test_general_knowledge_default(self):
        qs = self._pick("", 5, "medium")
        self.assertEqual(len(qs), 5)
        self.assertTrue(all("question" in q for q in qs))
        self.assertTrue(all("answer" in q for q in qs))
        self.assertTrue(all("options" in q for q in qs))

    def test_python_topic(self):
        qs = self._pick("python basics", 5, "medium")
        self.assertEqual(len(qs), 5)

    def test_history_topic(self):
        qs = self._pick("world history", 5, "medium")
        self.assertEqual(len(qs), 5)

    def test_math_topic(self):
        qs = self._pick("mathematics", 5, "medium")
        self.assertEqual(len(qs), 5)

    def test_count_capped_at_bank_size(self):
        from plugins.quiz import _PYTHON_BANK
        qs = self._pick("python", 100, "medium")
        self.assertEqual(len(qs), len(_PYTHON_BANK))

    def test_minimum_count(self):
        qs = self._pick("", 1, "medium")
        self.assertGreaterEqual(len(qs), 1)

    def test_questions_have_options(self):
        qs = self._pick("python", 5, "medium")
        for q in qs:
            self.assertIsInstance(q.get("options"), list)
            self.assertGreater(len(q["options"]), 0)


class TestQuizGrader(unittest.TestCase):
    """Test the grader function."""

    def setUp(self):
        from plugins.quiz import _grader
        self.grader = _grader

    def test_correct_mc_answer(self):
        q = {"type": "mc", "answer": "Mars"}
        self.assertTrue(self.grader(q, "Mars"))

    def test_wrong_mc_answer(self):
        q = {"type": "mc", "answer": "Mars"}
        self.assertFalse(self.grader(q, "Venus"))

    def test_case_insensitive(self):
        q = {"type": "mc", "answer": "Mars"}
        self.assertTrue(self.grader(q, "mars"))

    def test_open_ended_defers(self):
        q = {"type": "open", "answer": "some answer"}
        result = self.grader(q, "user response")
        self.assertIsNone(result)


class TestQuizRun(unittest.TestCase):
    """Test the run() function."""

    def test_run_returns_string(self):
        from plugins.quiz import run
        result = run({"topic": "python", "count": "3"})
        self.assertIsInstance(result, str)
        self.assertIn("quiz", result.lower())

    def test_run_with_player(self):
        from plugins.quiz import run
        player = MagicMock()
        result = run({"topic": "math", "count": "3"}, player=player)
        self.assertIsInstance(result, str)
        player.show_quiz.assert_called_once()

    def test_run_default_params(self):
        from plugins.quiz import run
        result = run({})
        self.assertIsInstance(result, str)
        self.assertIn("5", result)  # default count

    def test_run_invalid_count(self):
        from plugins.quiz import run
        result = run({"count": "abc"})
        self.assertIsInstance(result, str)
        player = MagicMock()
        run({"count": "abc"}, player=player)
        player.show_quiz.assert_called_once()

    def test_run_count_bounds(self):
        from plugins.quiz import run
        player = MagicMock()
        run({"count": "100"}, player=player)
        call_args = player.show_quiz.call_args
        questions = call_args[0][1]
        self.assertLessEqual(len(questions), 15)

    def test_run_count_minimum(self):
        from plugins.quiz import run
        player = MagicMock()
        run({"count": "1"}, player=player)
        call_args = player.show_quiz.call_args
        questions = call_args[0][1]
        self.assertGreaterEqual(len(questions), 3)

    def test_run_difficulty(self):
        from plugins.quiz import run
        result = run({"topic": "python", "difficulty": "hard"})
        self.assertIsInstance(result, str)
        self.assertIn("hard", result.lower())


# ── Calendar Plugin Tests ────────────────────────────────────────────────────

class TestCalendarPluginStructure(unittest.TestCase):
    """Verify the calendar plugin has the correct loader contract."""

    def setUp(self):
        from plugins import calendar as cal_mod
        self.mod = cal_mod

    def test_plugin_dict_exists(self):
        self.assertIsInstance(self.mod.PLUGIN, dict)

    def test_plugin_name(self):
        self.assertEqual(self.mod.PLUGIN["name"], "calendar")

    def test_plugin_description(self):
        self.assertTrue(len(self.mod.PLUGIN["description"]) > 20)

    def test_plugin_parameters_type(self):
        self.assertEqual(self.mod.PLUGIN["parameters"]["type"], "OBJECT")

    def test_run_callable(self):
        self.assertTrue(callable(self.mod.run))


class TestCalendarFormats(unittest.TestCase):
    """Test event formatting and date/time parsing."""

    def setUp(self):
        from plugins.calendar import _fmt_event, _parse_date, _parse_time, _now_local
        self.fmt = _fmt_event
        self.parse_date = _parse_date
        self.parse_time = _parse_time
        self.now = _now_local()

    def test_format_timed_event(self):
        ev = {
            "summary": "Meeting",
            "start": {"dateTime": "2025-06-15T10:00:00+00:00"},
            "end": {"dateTime": "2025-06-15T11:00:00+00:00"},
        }
        result = self.fmt(ev)
        self.assertIn("Meeting", result)
        self.assertIn("10:00", result)

    def test_format_all_day_event(self):
        ev = {
            "summary": "Holiday",
            "start": {"date": "2025-06-15"},
            "end": {"date": "2025-06-16"},
        }
        result = self.fmt(ev)
        self.assertIn("Holiday", result)
        self.assertIn("all day", result)

    def test_format_event_with_location(self):
        ev = {
            "summary": "Lunch",
            "start": {"dateTime": "2025-06-15T12:00:00+00:00"},
            "end": {"dateTime": "2025-06-15T13:00:00+00:00"},
            "location": "Cafe",
        }
        result = self.fmt(ev)
        self.assertIn("@ Cafe", result)

    def test_format_no_title(self):
        ev = {
            "start": {"dateTime": "2025-06-15T10:00:00+00:00"},
            "end": {"dateTime": "2025-06-15T11:00:00+00:00"},
        }
        result = self.fmt(ev)
        self.assertIn("(no title)", result)

    def test_parse_date_valid(self):
        d = self.parse_date("2025-06-15")
        self.assertEqual(d.year, 2025)
        self.assertEqual(d.month, 6)
        self.assertEqual(d.day, 15)

    def test_parse_date_invalid(self):
        d = self.parse_date("not-a-date")
        self.assertEqual(d.date(), self.now.date())

    def test_parse_date_empty(self):
        d = self.parse_date("")
        self.assertEqual(d.date(), self.now.date())

    def test_parse_time_valid(self):
        d = self.parse_time("14:30")
        self.assertEqual(d.hour, 14)
        self.assertEqual(d.minute, 30)

    def test_parse_time_invalid(self):
        d = self.parse_time("not-a-time")
        self.assertIsInstance(d.hour, int)

    def test_parse_time_empty(self):
        d = self.parse_time("")
        self.assertIsInstance(d.hour, int)


class TestCalendarRun(unittest.TestCase):
    """Test the run() function with mocked Google service."""

    def test_no_action(self):
        from plugins.calendar import run
        result = run({})
        self.assertIn("specify an action", result.lower())

    def test_unknown_action(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "bogus"})
            self.assertIn("unknown", result.lower())

    def test_no_credentials(self):
        from plugins.calendar import run
        with patch("plugins.calendar.get_service", None):
            result = run({"action": "today"})
            self.assertIn("not available", result.lower())

    def test_search_no_query(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "search", "query": ""})
            self.assertIn("provide a search keyword", result.lower())

    def test_create_no_title(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "create", "title": ""})
            self.assertIn("provide an event title", result.lower())

    def test_today_with_events(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.events().list().execute.return_value.get.return_value = [
            {
                "summary": "Team standup",
                "start": {"dateTime": "2025-06-15T09:00:00+00:00"},
                "end": {"dateTime": "2025-06-15T09:30:00+00:00"},
            }
        ]
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "today"})
            self.assertIn("Team standup", result)

    def test_today_empty(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.events().list().execute.return_value.get.return_value = []
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "today"})
            self.assertIn("clear", result.lower())

    def test_create_event(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {
            "summary": "Test Event",
            "htmlLink": "https://calendar.google.com/event",
        }
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "create", "title": "Test Event", "date": "2025-06-15", "time": "10:00"})
            self.assertIn("Created", result)
            self.assertIn("Test Event", result)

    def test_list_calendars(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.calendarList().list().execute.return_value.get.return_value = [
            {"summary": "Personal", "id": "primary", "primary": True},
            {"summary": "Work", "id": "work@group.calendar.google.com"},
        ]
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "list_calendars"})
            self.assertIn("Personal", result)
            self.assertIn("Work", result)
            self.assertIn("primary", result)

    def test_create_invalid_duration(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {
            "summary": "Test", "htmlLink": "",
        }
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            result = run({"action": "create", "title": "Test", "duration": "abc"})
            self.assertIn("Created", result)

    def test_create_duration_bounds(self):
        from plugins.calendar import run
        mock_service = MagicMock()
        mock_service.events().insert().execute.return_value = {
            "summary": "Test", "htmlLink": "",
        }
        with patch("plugins.calendar.get_service", return_value=mock_service), \
             patch("plugins.calendar.credentials_valid", return_value=True):
            run({"action": "create", "title": "Test", "duration": "99999"})
            call_kwargs = mock_service.events().insert.call_args
            body = call_kwargs[1]["body"]
            from datetime import datetime
            start = datetime.fromisoformat(body["start"]["dateTime"])
            end = datetime.fromisoformat(body["end"]["dateTime"])
            diff_minutes = (end - start).total_seconds() / 60
            self.assertLessEqual(diff_minutes, 480)


if __name__ == "__main__":
    unittest.main()
