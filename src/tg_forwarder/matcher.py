from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class KeywordMatcher:
    keywords: list[str]
    regex: Optional[re.Pattern[str]] = None

    @classmethod
    def build(cls, keywords: list[str], keyword_regex: str | None) -> "KeywordMatcher":
        compiled = re.compile(keyword_regex) if keyword_regex else None
        return cls(keywords=keywords, regex=compiled)

    def matches(self, text: str) -> bool:
        if not text:
            return False
        if self.regex and self.regex.search(text):
            return True
        t = text.lower()
        return any(k in t for k in self.keywords)