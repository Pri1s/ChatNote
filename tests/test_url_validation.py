from __future__ import annotations

import unittest

from chatnote.url_validation import URLValidationError, validate_claude_share_url


class URLValidationTests(unittest.TestCase):
    def test_accepts_claude_share_url(self) -> None:
        result = validate_claude_share_url("https://claude.ai/share/abc-123")

        self.assertEqual(result.conversation_id, "abc-123")

    def test_rejects_private_claude_chat_url(self) -> None:
        with self.assertRaises(URLValidationError):
            validate_claude_share_url("https://claude.ai/chat/abc-123")

    def test_rejects_non_claude_url(self) -> None:
        with self.assertRaises(URLValidationError):
            validate_claude_share_url("https://example.com/share/abc-123")

    def test_rejects_non_https_url(self) -> None:
        with self.assertRaises(URLValidationError):
            validate_claude_share_url("http://claude.ai/share/abc-123")


if __name__ == "__main__":
    unittest.main()
