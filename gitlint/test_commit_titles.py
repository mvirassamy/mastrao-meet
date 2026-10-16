"""Exercise both active commit conventions without a network dependency."""

import unittest
from unittest.mock import patch

from gitlint_emoji import GitmojiTitle


class CommitTitleTests(unittest.TestCase):
    def setUp(self):
        self.rule = GitmojiTitle()
        self.catalog = patch("gitlint_emoji.requests.get")
        self.get_catalog = self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.get_catalog.return_value.json.return_value = {
            "gitmojis": [{"emoji": "🐛"}, {"emoji": "✨"}]
        }

    def test_accepts_scoped_commitizen_without_catalog_lookup(self):
        for title in (
            "fix(ci): validate commit messages",
            "feat(meet)!: update the meeting contract",
            "test(core): cover signed status reads",
        ):
            with self.subTest(title=title):
                self.assertIsNone(self.rule.validate(title, None))
        self.get_catalog.assert_not_called()

    def test_preserves_gitmoji_validation(self):
        for title in ("🐛(ci) validate commit messages", "✨(meet) add filing"):
            with self.subTest(title=title):
                self.assertIsNone(self.rule.validate(title, None))

    def test_rejects_unstructured_or_incomplete_titles(self):
        for title in (
            "update the meeting",
            "unknown(ci): update the meeting",
            "fix: update the meeting",
            "fix(): update the meeting",
            "fix(ci): ",
            "fix(ci): Update the meeting",
            "🚫(ci) update the meeting",
        ):
            with self.subTest(title=title):
                violations = self.rule.validate(title, None)
                self.assertEqual([item.rule_id for item in violations], ["UC1"])
