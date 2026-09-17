"""
JARVIS Gmail Plugin Test Suite.

Tests the gmail.py plugin with mocked Google API service.
Verifies plugin structure, input validation, and handler logic
without requiring actual Gmail API credentials.
"""
import base64
import unittest
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_PLUGINS_DIR = PROJECT_ROOT / "plugins"
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))


class TestGmailPluginStructure(unittest.TestCase):
    """Verify the plugin meets the loader's contract."""

    def test_plugin_dict_exists(self):
        import gmail
        self.assertIsInstance(gmail.PLUGIN, dict)

    def test_plugin_name_is_valid(self):
        import gmail
        name = gmail.PLUGIN.get("name", "")
        self.assertEqual(name, "gmail")

    def test_plugin_description_nonempty(self):
        import gmail
        desc = gmail.PLUGIN.get("description", "")
        self.assertTrue(len(desc) > 20)

    def test_plugin_parameters_are_object(self):
        import gmail
        params = gmail.PLUGIN.get("parameters", {})
        self.assertEqual(params.get("type"), "OBJECT")

    def test_action_property_exists(self):
        import gmail
        props = gmail.PLUGIN.get("parameters", {}).get("properties", {})
        self.assertIn("action", props)

    def test_run_is_callable(self):
        import gmail
        self.assertTrue(callable(gmail.run))

    def test_run_returns_string(self):
        import gmail
        with patch("gmail._get_service", return_value=None):
            result = gmail.run({"action": "status"})
        self.assertIsInstance(result, str)


class TestGmailStatus(unittest.TestCase):
    """Test the status action (no API needed)."""

    def test_status_when_not_connected(self):
        import gmail
        with patch("gmail.credentials_valid", return_value=False):
            result = gmail.run({"action": "status"})
        self.assertIn("not connected", result.lower())

    def test_status_when_connected(self):
        import gmail
        with patch("gmail.credentials_valid", return_value=True):
            result = gmail.run({"action": "status"})
        self.assertIn("connected", result.lower())


class TestGmailNoService(unittest.TestCase):
    """Test behavior when the API service cannot be obtained."""

    def test_list_no_service(self):
        import gmail
        with patch("gmail._get_service", return_value=None):
            result = gmail.run({"action": "list"})
        self.assertIn("not connected", result.lower())

    def test_send_no_service(self):
        import gmail
        with patch("gmail._get_service", return_value=None):
            result = gmail.run({"action": "send", "to": "a@b.com", "subject": "Hi", "body": "Hello"})
        self.assertIn("not connected", result.lower())


class TestGmailListHandler(unittest.TestCase):
    """Test list/search with a mocked service."""

    def _mock_service(self, messages=None):
        svc = MagicMock()
        msgs = messages or []
        svc.users().messages().list().execute.return_value = {
            "messages": [{"id": m} for m in msgs],
            "resultSizeEstimate": len(msgs),
        }
        svc.users().messages().get().execute.return_value = {
            "id": "test123",
            "snippet": "Test snippet",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "Date", "value": "Mon, 1 Jan 2025"},
                ]
            },
        }
        return svc

    def test_list_empty_inbox(self):
        import gmail
        svc = self._mock_service(messages=[])
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "list"})
        self.assertIn("no emails", result.lower())

    def test_list_with_messages(self):
        import gmail
        svc = self._mock_service(messages=["msg1", "msg2"])
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "list"})
        self.assertIn("alice@example.com", result)
        self.assertIn("Test Subject", result)

    def test_search_empty_query(self):
        import gmail
        svc = self._mock_service()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "search", "query": ""})
        self.assertIn("provide a search query", result.lower())


class TestGmailReadHandler(unittest.TestCase):
    """Test read with a mocked service."""

    def test_read_no_message_id(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "read", "message_id": ""})
        self.assertIn("provide a message_id", result.lower())

    def test_read_with_message_id(self):
        import gmail
        svc = MagicMock()
        body_data = base64.urlsafe_b64encode(b"Hello from Gmail!").decode()
        svc.users().messages().get().execute.return_value = {
            "id": "abc123",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "bob@test.com"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Date", "value": "Tue, 2 Jan 2025"},
                ],
                "body": {"data": body_data},
            },
        }
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "read", "message_id": "abc123"})
        self.assertIn("bob@test.com", result)
        self.assertIn("Hello", result)
        self.assertIn("Hello from Gmail!", result)
        self.assertIn("INBOX", result)


class TestGmailSendHandler(unittest.TestCase):
    """Test send validation and execution."""

    def test_send_no_to(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "send", "to": "", "subject": "Hi", "body": "Hello"})
        self.assertIn("provide a recipient", result.lower())

    def test_send_no_subject(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "send", "to": "a@b.com", "subject": "", "body": "Hello"})
        self.assertIn("provide an email subject", result.lower())

    def test_send_no_body(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "send", "to": "a@b.com", "subject": "Hi", "body": ""})
        self.assertIn("provide the email body", result.lower())

    def test_send_success(self):
        import gmail
        svc = MagicMock()
        svc.users().messages().send().execute.return_value = {"id": "sent123"}
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({
                "action": "send",
                "to": "alice@example.com",
                "subject": "Test",
                "body": "Hello Alice",
            })
        self.assertIn("sent to alice@example.com", result.lower())
        self.assertIn("sent123", result)


class TestGmailUnreadCount(unittest.TestCase):
    """Test unread_count handler."""

    def test_zero_unread(self):
        import gmail
        svc = MagicMock()
        svc.users().messages().list().execute.return_value = {
            "resultSizeEstimate": 0,
        }
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "unread_count"})
        self.assertIn("no unread", result.lower())

    def test_some_unread(self):
        import gmail
        svc = MagicMock()
        svc.users().messages().list().execute.return_value = {
            "resultSizeEstimate": 5,
        }
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "unread_count"})
        self.assertIn("5 unread", result)


class TestGmailMarkReadAndDelete(unittest.TestCase):
    """Test mark_read and delete handlers."""

    def test_mark_read_no_id(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "mark_read", "message_id": ""})
        self.assertIn("provide a message_id", result.lower())

    def test_mark_read_success(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "mark_read", "message_id": "xyz"})
        self.assertIn("marked as read", result.lower())

    def test_delete_no_id(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "delete", "message_id": ""})
        self.assertIn("provide a message_id", result.lower())

    def test_delete_success(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "delete", "message_id": "xyz"})
        self.assertIn("moved to trash", result.lower())


class TestGmailLabels(unittest.TestCase):
    """Test labels handler."""

    def test_labels_success(self):
        import gmail
        svc = MagicMock()
        svc.users().labels().list().execute.return_value = {
            "labels": [
                {"id": "INBOX", "name": "INBOX"},
                {"id": "SENT", "name": "SENT"},
            ],
        }
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "labels"})
        self.assertIn("INBOX", result)
        self.assertIn("SENT", result)

    def test_labels_empty(self):
        import gmail
        svc = MagicMock()
        svc.users().labels().list().execute.return_value = {"labels": []}
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "labels"})
        self.assertIn("no labels", result.lower())


class TestGmailUnknownAction(unittest.TestCase):
    """Test unknown action handling."""

    def test_unknown_action(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": "foobar"})
        self.assertIn("unknown gmail action", result.lower())

    def test_empty_action(self):
        import gmail
        svc = MagicMock()
        with patch("gmail._get_service", return_value=svc):
            result = gmail.run({"action": ""})
        self.assertIn("unknown gmail action", result.lower())


class TestGmailHelpers(unittest.TestCase):
    """Test internal helper functions."""

    def test_headers_conversion(self):
        import gmail
        headers = [
            {"name": "From", "value": "test@test.com"},
            {"name": "Subject", "value": "Hello"},
        ]
        result = gmail._headers(headers)
        self.assertEqual(result["from"], "test@test.com")
        self.assertEqual(result["subject"], "Hello")

    def test_parse_max_default(self):
        import gmail
        self.assertEqual(gmail._parse_max({}), 10)

    def test_parse_max_custom(self):
        import gmail
        self.assertEqual(gmail._parse_max({"max_results": "5"}), 5)

    def test_parse_max_invalid(self):
        import gmail
        self.assertEqual(gmail._parse_max({"max_results": "abc"}), 10)

    def test_parse_max_bounds(self):
        import gmail
        self.assertEqual(gmail._parse_max({"max_results": "0"}), 1)
        self.assertEqual(gmail._parse_max({"max_results": "100"}), 50)

    def test_decode_body_plain(self):
        import gmail
        data = base64.urlsafe_b64encode(b"Hello World").decode()
        payload = {"mimeType": "text/plain", "body": {"data": data}}
        self.assertEqual(gmail._decode_body(payload), "Hello World")

    def test_decode_body_empty(self):
        import gmail
        self.assertEqual(gmail._decode_body({"mimeType": "text/plain", "body": {}}), "")

    def test_decode_body_multipart(self):
        import gmail
        data = base64.urlsafe_b64encode(b"Nested body").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": data}},
            ],
        }
        self.assertEqual(gmail._decode_body(payload), "Nested body")

    def test_snippet_format(self):
        import gmail
        msg = {
            "snippet": "This is a test",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@test.com"},
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "Date", "value": "Wed, 3 Jan 2025"},
                ]
            },
        }
        result = gmail._snippet(msg)
        self.assertIn("alice@test.com", result)
        self.assertIn("Test Email", result)
        self.assertIn("This is a test", result)


if __name__ == "__main__":
    unittest.main()
