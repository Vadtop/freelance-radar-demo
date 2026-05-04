from __future__ import annotations

import re

BANNED_PHRASES = [
    "готов обсудить",
    "жду ответа",
    "регулярные апдейты",
    "прозрачный план",
    "начать сегодня",
    "есть опыт",
    "качественно и в срок",
    "индивидуальный подход",
    "ответственный подход",
    "быстро и качественно",
    "большой опыт",
    "многолетний опыт",
    "готов помочь",
    "жду вашего ответа",
    "с удовольствием помогу",
    "обращайтесь",
    "надеюсь на сотрудничество",
    "буду рад сотрудничать",
    "внимание к деталям",
    "строго в срок",
    "точно в срок",
    "не подведу",
    "гарантия качества",
    "высокое качество",
    "профессионально",
    "компетентно",
]

BANNED_GREETINGS = [
    "здравствуйте",
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "приветствую",
    "хай",
    "хеллоу",
]

BANNED_REGEX_PATTERNS = [
    re.compile(r"^[^а-яёА-ЯЁ]*((здравствуйте|добрый\s+(день|вечер|утро)|приветствую))", re.I | re.S),
    re.compile(r"(жду\s+(вашего\s+)?ответа|готов\s+обсудить|начать\s+сегодня)", re.I),
    re.compile(r"((качественно|быстро|профессионально)\s+и\s+(в\s+срок|качественно|надёжно))", re.I),
    re.compile(r"(есть\s+опыт|большой\s+опыт|многолетний\s+опыт)", re.I),
    re.compile(r"(прозрачный\s+план|регулярные\s+апдейты)", re.I),
]


def check_banned(text: str) -> list[str]:
    found = []
    lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            found.append(phrase)
    for greeting in BANNED_GREETINGS:
        if lower.strip().startswith(greeting):
            found.append(greeting)
            break
    for pat in BANNED_REGEX_PATTERNS:
        m = pat.search(text)
        if m:
            found.append(m.group(0).strip())
    return list(set(found))
