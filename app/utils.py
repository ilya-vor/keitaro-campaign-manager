"""Мелкие утилиты: транслит, alias, нормализация гео и весов офферов."""
from __future__ import annotations

import re

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}

_ALIAS_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 60) -> str:
    """'Тест кампания #1' -> 'test_kampaniya_1' (alias для URL Keitaro)."""
    lowered = value.strip().lower()
    transliterated = "".join(TRANSLIT.get(ch, ch) for ch in lowered)
    slug = _ALIAS_RE.sub("_", transliterated).strip("_")
    if not slug:
        slug = "campaign"
    return slug[:max_length].strip("_")


def normalize_geo(raw: str | list[str] | None) -> list[str]:
    """'au, ro;AU' -> ['AU', 'RO'] (ISO-3166 alpha-2, верхний регистр, без дублей)."""
    if raw is None:
        return []
    chunks: list[str] = []
    if isinstance(raw, str):
        chunks = re.split(r"[,;\s]+", raw)
    else:
        for item in raw:
            if isinstance(item, str):
                chunks.extend(re.split(r"[,;\s]+", item))
    result: list[str] = []
    for chunk in chunks:
        code = chunk.strip().upper()
        if not code:
            continue
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError(
                f"Гео-код «{chunk}» выглядит некорректно: нужен ISO-3166 alpha-2, например AU или RO"
            )
        if code not in result:
            result.append(code)
    if not result:
        raise ValueError("Укажите хотя бы одну страну (гео-код), например AU")
    return result


def normalize_shares(items: list[dict], *, total: int = 100) -> list[dict]:
    """Приводит веса офферов к целым процентам с суммой 100.

    items: [{'offer_id': int, 'share': int | None}, ...]
    """
    if not items:
        return []

    seen: set[int] = set()
    cleaned: list[dict] = []
    for item in items:
        offer_id = int(item["offer_id"])
        if offer_id in seen:
            raise ValueError(f"Оффер {offer_id} указан дважды")
        seen.add(offer_id)
        share = item.get("share")
        cleaned.append({"offer_id": offer_id, "share": max(int(share), 0) if share is not None else None})

    missing = [row for row in cleaned if row["share"] is None]
    known_sum = sum(row["share"] for row in cleaned if row["share"] is not None)

    if missing and len(missing) == len(cleaned):
        return _equal_shares(cleaned, total)

    if missing:
        rest = total - known_sum
        if rest < len(missing):
            # Свободного места нет — честнее поделить всё поровну.
            return _equal_shares(cleaned, total)
        per = rest // len(missing)
        rem = rest - per * len(missing)
        for index, row in enumerate(missing):
            row["share"] = per + (1 if index < rem else 0)

    current = sum(row["share"] for row in cleaned)
    if current == total:
        return cleaned
    if current == 0:
        return _equal_shares(cleaned, total)

    # Пропорциональная нормализация с «крупнейшим остатком», чтобы сумма была ровно 100.
    scaled = [(row["share"] * total) / current for row in cleaned]
    floors = [int(value) for value in scaled]
    leftover = total - sum(floors)
    order = sorted(range(len(scaled)), key=lambda i: scaled[i] - floors[i], reverse=True)
    for index in order[:leftover]:
        floors[index] += 1
    for row, share in zip(cleaned, floors):
        row["share"] = share
    return cleaned


def _equal_shares(rows: list[dict], total: int) -> list[dict]:
    base = total // len(rows)
    remainder = total - base * len(rows)
    for index, row in enumerate(rows):
        row["share"] = base + (1 if index < remainder else 0)
    return rows
