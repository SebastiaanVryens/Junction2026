"""Our topics: war, Russia and Ukraine, NATO, the EU, the Baltics and Finland, elections, sanctions.

A group of accounts is "on our topics" when its shared text or Nemotron's English summary of it
mentions one of these words, in English, Russian, Ukrainian, Finnish, Swedish, Estonian, Latvian or
German. Used to put these groups first for reviewers and to report their precision separately; the
random review sample ignores it, so the false-positive rate stays honest.
"""

from __future__ import annotations

import re

# Word starts (matched anywhere): they cover inflections in every language listed.
STEMS = (
    "ukrain", "україн", "украин", "russia", "росси", "росія", "venäjä", "ryssland", "venemaa", "krievij",
    "russland", "nato", "нато", "putin", "путин", "путін", "zelensk", "зеленск", "зеленськ", "kremlin",
    "кремл", "donbas", "донбас", "crimea", "крым", "крим", "kyiv", "kiev", "киев", "київ", "moscow",
    "москв", "military", "militär", "armeij", "армия", "армі", "drone", "дрон", "missile", "ракет",
    "sanction", "санкц", "pakote", "election", "выбор", "вибор", "vaali", "propagand", "пропаганд",
    "finland", "suomi", "suomen", "финлянд", "фінлянд", "eston", "эстон", "естон", "latvi", "латв",
    "lithuan", "литв", "литов", "baltic", "балт", "poland", "польш", "europe", "европ", "європ",
    "nuclear", "ядерн", "ceasefire", "перемири", "mobiliz", "мобилиз", "мобіліз", "wagner", "вагнер",
    "invasion", "вторжен", "occupation", "оккупац", "окупац", "frontline", "фронт",
)  # fmt: skip
# Short words that must stand alone ("war" but not "warm").
WORDS = ("war", "eu", "sota", "krig", "sõda", "karš", "krieg", "война", "війна", "всу", "зсу")

PYTHON_PATTERN = re.compile(
    "(" + "|".join(map(re.escape, STEMS)) + r")|\b(" + "|".join(map(re.escape, WORDS)) + r")\b", re.IGNORECASE
)
# PostgreSQL's regular expressions mark word edges with \y (\b means backspace there).
SQL_PATTERN = "(" + "|".join(STEMS) + r")|\y(" + "|".join(WORDS) + r")\y"


def on_topic(text: str | None) -> bool:
    return bool(text) and bool(PYTHON_PATTERN.search(text or ""))
