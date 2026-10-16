"""
Validate scoped Gitmoji titles and conventional Commitizen titles.
"""

import re

import requests
from gitlint.rules import CommitMessageTitle, LineRule, RuleViolation


class GitmojiTitle(LineRule):
    """
    Accept the repository's Gitmoji convention and scoped Commitizen titles.
    Both forms require a lowercase subject; other gitlint rules still apply.
    """

    id = "UC1"
    name = "title-should-have-gitmoji-and-scope"
    target = CommitMessageTitle

    def validate(self, title, _commit):
        """
        Validate Commitizen directly, or check the existing Gitmoji convention.
        """
        conventional = (
            r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
            r"\([a-z0-9][a-z0-9._/-]*\)!?: [a-z].*$"
        )
        if re.fullmatch(conventional, title):
            return None

        gitmojis = requests.get(
            "https://raw.githubusercontent.com/carloscuesta/gitmoji/master/packages/gitmojis/src/gitmojis.json"
        ).json()["gitmojis"]
        emojis = [item["emoji"] for item in gitmojis]
        pattern = r"^({:s})\(.*\)\s[a-z].*$".format("|".join(emojis))
        if not re.search(pattern, title):
            violation_msg = 'Use "type(scope): subject" or "<gitmoji>(scope) subject"'
            return [RuleViolation(self.id, violation_msg, title)]
