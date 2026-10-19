"""Загрузка учебного материала (несколько частей книги) и построение вопросов."""
from __future__ import annotations

import html
import json
import random
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent

_AR = re.compile(r"\{\{(.*?)\}\}")


def tg(s: str) -> str:
    """Текст с {{арабским}} -> HTML для Telegram (арабское выделяется жирным)."""
    return _AR.sub(lambda m: "<b>%s</b>" % m.group(1), html.escape(s, quote=False))


def plain(s: str) -> str:
    return _AR.sub(lambda m: m.group(1), s)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def load_words(filename: str, key_prefix: str = "") -> list[dict]:
    """Читает words*.txt. key — ключ карточки в базе (у части 2 с префиксом, чтобы части не пересекались)."""
    words, seen = [], set()
    path = BASE / filename
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        unit, ar, ru = int(parts[0]), parts[1], parts[2]
        if ar in seen:
            continue
        seen.add(ar)
        words.append({"key": key_prefix + ar, "ar": ar, "ru": ru, "unit": unit})
    return words


def _shuffled(options: list[str], correct_idx: int, rng: random.Random):
    idx = list(range(len(options)))
    rng.shuffle(idx)
    return [options[i] for i in idx], idx.index(correct_idx)


class Part:
    """Одна часть книги: уроки, итоговый тест и слова."""

    def __init__(self, n: int, data_file: str, words_file: str, key_prefix: str, label: str):
        data = json.loads((BASE / data_file).read_text(encoding="utf-8"))
        self.n = n
        self.label = label                      # короткое описание для кнопки выбора части
        self.units: dict[int, dict] = {u["n"]: u for u in data["units"]}
        self.final = data["final"]
        self.words = load_words(words_file, key_prefix)
        self.words_by_key = {w["key"]: w for w in self.words}

    @property
    def title(self) -> str:
        return "«Арабский перед тобой», том 1, часть %d" % self.n

    def words_in_scope(self, scope: str) -> list[dict]:
        if scope in ("0", "D"):
            return list(self.words)
        return [w for w in self.words if w["unit"] == int(scope)]

    # ---------- построение вопросов ----------
    # choice: {"kind": "choice", "text", "options", "correct", "unit", "brief"}
    # self:   {"kind": "self", "text", "answer", "unit", "brief", "answer_plain"}

    def mcq_items(self, unit: int, rng: random.Random) -> list[dict]:
        items = []
        for q in self.units[unit]["mcq"]:
            opts, c = _shuffled(q["opts"], q["c"], rng)
            items.append({"kind": "choice", "text": "<b>Выбери правильный вариант</b>\n\n" + tg(q["q"]),
                          "options": opts, "correct": c, "unit": unit, "brief": esc(plain(q["q"]))})
        return items


    def final_mcq_items(self, rng: random.Random) -> list[dict]:
        items = []
        for q in self.final["mcq"]:
            opts, c = _shuffled(q["opts"], q["c"], rng)
            items.append({"kind": "choice", "text": "<b>Выбери правильный вариант</b>\n\n" + tg(q["q"]),
                          "options": opts, "correct": c, "unit": q["u"], "brief": esc(plain(q["q"]))})
        return items


    def fill_items(self, unit: int, rng: random.Random, only_blank: int | None = None) -> list[dict]:
        f = self.units[unit]["fill"]
        answers, bank = f["answers"], f["bank"]
        items = []
        blanks = range(1, len(answers) + 1) if only_blank is None else [only_blank]
        for k in blanks:
            rows, current_line = [], ""
            for li, line in enumerate(f["lines"]):
                def sub(m, k=k):
                    n = int(m.group(1))
                    return "❓ ______" if n == k else answers[n - 1]
                txt = re.sub(r"\[\[(\d+)\]\]", sub, line)
                rows.append("%d. %s\n<i>%s</i>" % (li + 1, esc(txt), esc(f["hints"][li])))
                if "[[%d]]" % k in line:
                    current_line = txt
            correct = answers[k - 1]
            others = [w for w in bank if w != correct]
            rng.shuffle(others)
            options = [correct] + others[:3]
            rng.shuffle(options)
            items.append({"kind": "choice",
                          "text": "<b>Вставь пропущенное в диалоге</b>\n\n" + "\n".join(rows),
                          "options": options, "correct": options.index(correct), "unit": unit,
                          "brief": esc(current_line)})
        return items


    def translation_items(self, unit: int, rng: random.Random) -> list[dict]:
        u = self.units[unit]
        items = []
        for ar, ru in u["a2r"]:
            items.append({"kind": "self", "text": "<b>Переведи на русский</b>\n\n<b>%s</b>" % esc(ar),
                          "answer": esc(ru), "answer_plain": esc(ru), "unit": unit, "brief": esc(ar)})
        for ru, ar in u["r2a"]:
            items.append({"kind": "self", "text": "<b>Переведи на арабский</b>\n\n%s" % esc(ru),
                          "answer": "<b>%s</b>" % esc(ar), "answer_plain": esc(ar), "unit": unit, "brief": esc(ru)})
        return items


    def final_translation_items(self) -> list[dict]:
        items = []
        for u, ar, ru in self.final["a2r"]:
            items.append({"kind": "self", "text": "<b>Переведи на русский</b>\n\n<b>%s</b>" % esc(ar),
                          "answer": esc(ru), "answer_plain": esc(ru), "unit": u, "brief": esc(ar)})
        for u, ru, ar in self.final["r2a"]:
            items.append({"kind": "self", "text": "<b>Переведи на арабский</b>\n\n%s" % esc(ru),
                          "answer": "<b>%s</b>" % esc(ar), "answer_plain": esc(ar), "unit": u, "brief": esc(ru)})
        return items


    def word_items(self, scope: str, n: int, rng: random.Random) -> list[dict]:
        pool = self.words_in_scope(scope)
        if len(pool) < 4:
            pool = list(self.words)
        chosen = rng.sample(pool, min(n, len(pool)))
        items = []
        for w in chosen:
            ar_to_ru = rng.random() < 0.5
            if ar_to_ru:
                correct, field_, prompt = w["ru"], "ru", "Как переводится слово?\n\n<b>%s</b>" % esc(w["ar"])
            else:
                correct, field_, prompt = w["ar"], "ar", "Как по-арабски?\n\n%s" % esc(w["ru"])
            candidates = [x for x in pool if x[field_] != correct]
            if len(candidates) < 3:
                candidates = [x for x in self.words if x[field_] != correct]
            rng.shuffle(candidates)
            options, seen = [correct], {correct}
            for x in candidates:
                if x[field_] not in seen:
                    options.append(x[field_])
                    seen.add(x[field_])
                if len(options) == 4:
                    break
            rng.shuffle(options)
            items.append({"kind": "choice", "text": "<b>Слово</b>\n\n" + prompt, "options": options,
                          "correct": options.index(correct), "unit": w["unit"],
                          "brief": esc(w["ar"] if ar_to_ru else w["ru"])})
        return items


    def build_quiz(self, scope: str, kind: str, rng: random.Random | None = None) -> list[dict]:
        rng = rng or random.Random()
        if kind == "final":
            items = self.final_mcq_items(rng) + self.final_translation_items()
            return items
        if kind == "quick":
            pool = []
            for n in self.units:
                pool += self.mcq_items(n, rng) + self.fill_items(n, rng)
            picked = rng.sample(pool, 7) + self.word_items("0", 3, rng)
            rng.shuffle(picked)
            return picked
        n = int(scope)
        if kind == "mcq":
            return self.mcq_items(n, rng)
        if kind == "fill":
            return self.fill_items(n, rng)
        if kind == "tr":
            return self.translation_items(n, rng)
        if kind == "wq":
            return self.word_items(str(n), 10, rng)
        raise ValueError(kind)


# ---------- список частей ----------
PARTS: dict[int, Part] = {
    1: Part(1, "data.json", "words.txt", "", "Часть 1 · единицы 1–8 (уроки 1–72)"),
    2: Part(2, "data2.json", "words2.txt", "2|", "Часть 2 · единицы 9–16 (уроки 73–144)"),
}
