"""Логика бота без привязки к Telegram: нажатие кнопки (callback_data) -> ответ.

Благодаря этому всю логику можно проверять тестами, а bot.py остаётся тонкой обёрткой.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

import content as C
from db import DB

Button = tuple[str, str]  # (текст, callback_data)
HOME: list[Button] = [("🏠 Меню", "m:main")]
CARDS_PER_SESSION = 20


@dataclass
class Resp:
    chunks: list[str]
    rows: list[list[Button]] = field(default_factory=list)
    toast: str | None = None


@dataclass
class Quiz:
    scope: str
    kind: str
    items: list[dict]
    i: int = 0
    score: int = 0
    answered: bool = False
    revealed: bool = False
    wrong: list[tuple[str, str, int]] = field(default_factory=list)  # (вопрос, ответ, урок)


@dataclass
class Cards:
    scope: str
    queue: list[str]
    i: int = 0
    shown: bool = False
    known: int = 0
    again: int = 0
    requeued: set = field(default_factory=set)


def chunk_text(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip("\n"))
    return chunks


def one(text: str, rows: list[list[Button]] | None = None) -> Resp:
    return Resp(chunk_text(text), rows or [])


def toast(msg: str) -> Resp:
    return Resp([], [], msg)


STALE = "Сессия закончилась. Открой раздел заново."
TIME_RE = re.compile(r"^\s*(\d{1,2})[:.]?(\d{2})\s*$")


class Engine:
    def __init__(self, db: DB, tz_name: str = "Europe/Moscow",
                 today_fn: Callable[[], date] | None = None,
                 rng: random.Random | None = None):
        self.db = db
        self.tz_name = tz_name
        self._today = today_fn or date.today
        self.rng = rng or random.Random()
        self.quizzes: dict[int, Quiz] = {}
        self.cards: dict[int, Cards] = {}

    # ------------------------------------------------------------ вход
    def handle(self, uid: int, data: str) -> Resp:
        self.db.ensure_user(uid)
        p = data.split(":")
        try:
            h = p[0]
            if h == "m":
                return self._menu(uid, p[1])
            if h == "p":
                return self._set_part(uid, int(p[1]))
            if h == "r":
                return self._rules(uid, int(p[1]))
            if h == "w":
                return self._words(uid, p)
            if h == "c":
                return self._card_action(uid, p)
            if h == "t":
                return self._test_menu(uid, int(p[2]))
            if h == "q":
                return self._quiz_start(uid, p)
            if h == "a":
                return self._answer(uid, int(p[1]), int(p[2]))
            if h == "g":
                return self._self_graded(uid, int(p[1]), p[2])
            if h == "nx":
                return self._next(uid, int(p[1]))
            if h == "n":
                return self._notify(uid, p)
        except (IndexError, ValueError, KeyError):
            pass
        return self._menu(uid, "main")

    # ------------------------------------------------------------ меню
    def _part(self, uid: int) -> C.Part:
        """Часть книги, которую пользователь сейчас изучает (по умолчанию — первая)."""
        return C.PARTS.get(self.db.get_part(uid) or 1, C.PARTS[1])

    def _part_choice(self, uid: int) -> Resp:
        cur = self.db.get_part(uid)
        text = "📚 <b>«Арабский перед тобой», том 1</b>\n\nКакую часть будем изучать?"
        if cur in C.PARTS:
            text += "\nСейчас выбрана: <b>часть %d</b>." % cur
        rows = [[("📘 " + C.PARTS[1].label, "p:1")], [("📗 " + C.PARTS[2].label, "p:2")]]
        if cur in C.PARTS:
            rows.append(HOME)
        return one(text, rows)

    def _set_part(self, uid: int, n: int) -> Resp:
        if n not in C.PARTS:
            raise ValueError
        self.db.set_part(uid, n)
        self.quizzes.pop(uid, None)
        self.cards.pop(uid, None)
        return self._menu(uid, "main")

    def counts(self, uid: int) -> tuple[int, int]:
        part = self._part(uid)
        state = self.db.cards_state(uid)
        today = self._today().isoformat()
        due = sum(1 for k, (_, d) in state.items() if k in part.words_by_key and d <= today)
        new = sum(1 for k in part.words_by_key if k not in state)
        return due, new

    def _menu(self, uid: int, which: str) -> Resp:
        part = self._part(uid)
        if which == "part":
            return self._part_choice(uid)
        if which == "words":
            return self._words_menu(uid)
        if which == "rules":
            rows = [[("%d. %s" % (n, u["ru"]), "r:%d" % n)] for n, u in part.units.items()]
            return one("📖 <b>Правила</b> · часть %d\n\nВыбери урок:" % part.n, rows + [HOME])
        if which == "tests":
            rows = [[("%d. %s" % (n, u["ru"]), "t:u:%d" % n)] for n, u in part.units.items()]
            rows.append([("🏁 Итоговый тест (20 вопросов)", "q:F:final")])
            rows.append([("🎯 Быстрый тест (10 вопросов)", "q:Q:quick")])
            rows.append(HOME)
            return one("✏️ <b>Тесты и упражнения</b> · часть %d\n\nВыбери урок или общий тест:" % part.n, rows)
        if which == "rem":
            return self._notify_menu(uid)
        self.quizzes.pop(uid, None)
        self.cards.pop(uid, None)
        rows = [[("🃏 Слова и карточки", "m:words")],
                [("📖 Правила", "m:rules")],
                [("✏️ Тесты и упражнения", "m:tests")],
                [("⏰ Напоминания", "m:rem")],
                [("🔀 Сменить часть", "m:part")]]
        return one("🏠 <b>Главное меню</b>\n%s.\nВыбери раздел:" % part.title, rows)

    # ------------------------------------------------------------ правила
    def _rules(self, uid: int, n: int) -> Resp:
        u = self._part(uid).units[n]
        parts = ["📖 <b>Урок %d. %s</b>" % (n, C.esc(u["ru"])), "<i>%s</i>" % C.esc(u["lessons"]), "",
                 "🎯 " + C.tg(u["goal"]), ""]
        for title, text in u["rules"]:
            parts.append("🔹 <u>%s</u>\n%s\n" % (C.esc(C.plain(title)), C.tg(text)))
        rows = [[("✏️ Тест по этому уроку", "t:u:%d" % n)], [("⬅️ К урокам", "m:rules")], HOME]
        return one("\n".join(parts), rows)

    # ------------------------------------------------------------ слова
    def _words_menu(self, uid: int) -> Resp:
        part = self._part(uid)
        due, new = self.counts(uid)
        rows = [[("%d. %s" % (n, u["ru"]), "w:u:%d" % n)] for n, u in part.units.items()]
        rows.append([("📚 Все слова (%d)" % len(part.words), "w:u:0")])
        rows.append([("🔔 Карточки на сегодня", "w:c:D:d")])
        rows.append(HOME)
        text = "🃏 <b>Слова и карточки</b> · часть %d\n\nК повторению: <b>%d</b> · новых слов: <b>%d</b>\nВыбери урок:" % (part.n, due, new)
        return one(text, rows)

    def _scope_title(self, uid: int, scope: str) -> str:
        if scope in ("0", "D"):
            return "все уроки, часть %d" % self._part(uid).n
        return "урок %s. %s" % (scope, self._part(uid).units[int(scope)]["ru"])

    def _words(self, uid: int, p: list[str]) -> Resp:
        act, scope = p[1], p[2]
        part = self._part(uid)
        if scope not in ("0", "D") and int(scope) not in part.units:
            raise ValueError
        if act == "u":
            words = part.words_in_scope(scope)
            rows = [[("📋 Список слов", "w:l:%s" % scope)],
                    [("🃏 Карточки (новые и к повторению)", "w:c:%s:d" % scope)],
                    [("🔄 Пройти все слова подряд", "w:c:%s:a" % scope)],
                    [("⬅️ К урокам", "m:words")]]
            return one("🃏 <b>%s</b>\nСлов: %d" % (C.esc(self._scope_title(uid, scope)), len(words)), rows)
        if act == "l":
            words = part.words_in_scope(scope)
            lines = ["📋 <b>Слова — %s</b> (%s)\n" % (C.esc(self._scope_title(uid, scope)), len(words))]
            lines += ["%d. <b>%s</b> — %s" % (i, C.esc(w["ar"]), C.esc(w["ru"])) for i, w in enumerate(words, 1)]
            rows = [[("🃏 Карточки", "w:c:%s:d" % scope)], [("⬅️ Назад", "w:u:%s" % scope)], HOME]
            return one("\n".join(lines), rows)
        if act == "c":
            return self._cards_start(uid, scope, p[3])
        raise ValueError

    # ------------------------------------------------------------ карточки
    def _cards_start(self, uid: int, scope: str, mode: str) -> Resp:
        words = self._part(uid).words_in_scope(scope)
        if mode == "d":
            state = self.db.cards_state(uid)
            today = self._today().isoformat()
            keys = [w["key"] for w in words if w["key"] not in state or state[w["key"]][1] <= today]
        else:
            keys = [w["key"] for w in words]
        if not keys:
            rows = [[("🔄 Повторить всё равно", "w:c:%s:a" % scope)], [("⬅️ К словам", "m:words")], HOME]
            return one("✅ На сегодня карточек нет: всё повторено.", rows)
        self.rng.shuffle(keys)
        self.quizzes.pop(uid, None)
        self.cards[uid] = Cards(scope=scope, queue=keys[:CARDS_PER_SESSION])
        return self._card_view(uid)

    def _card_view(self, uid: int) -> Resp:
        cs = self.cards[uid]
        w = self._part(uid).words_by_key[cs.queue[cs.i]]
        head = "🃏 <b>Карточка %d/%d</b>\n\n<b>%s</b>" % (cs.i + 1, len(cs.queue), C.esc(w["ar"]))
        stop = [("⛔ Закончить", "c:x")]
        if not cs.shown:  # сразу «Знаю / Не знаю», без промежуточного шага
            return one(head, [[("✅ Знаю", "c:y:%d" % cs.i), ("🔁 Не знаю", "c:n:%d" % cs.i)], stop])
        # после «Не знаю» показываем перевод, чтобы запомнить
        return one(head + "\n— " + C.esc(w["ru"]), [[("➡️ Дальше", "c:d:%d" % cs.i)], stop])

    def _card_next(self, uid: int) -> Resp:
        cs = self.cards[uid]
        cs.i += 1
        cs.shown = False
        if cs.i >= len(cs.queue):
            return self._card_finish(uid)
        return self._card_view(uid)

    def _card_finish(self, uid: int, stopped: bool = False) -> Resp:
        cs = self.cards.pop(uid)
        total = cs.known + cs.again
        title = "⛔ Остановлено" if stopped else "🏁 Карточки закончились"
        text = "%s\n\nОтвечено: %d\n✅ Знаю: %d\n🔁 Не знаю: %d" % (title, total, cs.known, cs.again)
        rows = [[("🃏 Ещё карточки", "w:c:%s:d" % cs.scope)], [("⬅️ К словам", "m:words")], HOME]
        return one(text, rows)

    def _card_action(self, uid: int, p: list[str]) -> Resp:
        cs = self.cards.get(uid)
        if cs is None:
            return toast(STALE)
        act = p[1]
        if act == "x":
            return self._card_finish(uid, stopped=True)
        i = int(p[2])
        if i != cs.i:
            return toast("Эта карточка уже пройдена.")
        if act == "s":  # кнопка из старого сообщения: просто показать текущую карточку
            return self._card_view(uid)
        if act in ("y", "n"):
            if cs.shown:
                return toast("Сначала нажми «Дальше».")
            key = cs.queue[cs.i]
            known = act == "y"
            self.db.review(uid, key, known, self._today())
            if known:
                cs.known += 1
                return self._card_next(uid)
            cs.again += 1
            if key not in cs.requeued:
                cs.requeued.add(key)
                cs.queue.append(key)
            cs.shown = True
            return self._card_view(uid)
        if act == "d":
            if not cs.shown:
                return toast("Это действие уже выполнено.")
            return self._card_next(uid)
        raise ValueError

    # ------------------------------------------------------------ тесты
    def _test_menu(self, uid: int, n: int) -> Resp:
        u = self._part(uid).units[n]
        rows = [[("✅ Выбор правильной формы", "q:%d:mcq" % n)],
                [("🧩 Вставь пропущенное в диалог", "q:%d:fill" % n)],
                [("🔤 Перевод (арабский ↔ русский)", "q:%d:tr" % n)],
                [("📝 Тест по словам урока", "q:%d:wq" % n)],
                [("📖 Правила урока", "r:%d" % n)],
                [("⬅️ К урокам", "m:tests")], HOME]
        return one("✏️ <b>Урок %d. %s</b>\n\nВыбери тип упражнений:" % (n, C.esc(u["ru"])), rows)

    def _quiz_start(self, uid: int, p: list[str]) -> Resp:
        if p[1] == "stop":
            qz = self.quizzes.pop(uid, None)
            if qz is None:
                return toast(STALE)
            return self._quiz_finish(uid, qz, stopped=True, pop=False)
        scope, kind = p[1], p[2]
        items = self._part(uid).build_quiz(scope, kind, self.rng)
        self.cards.pop(uid, None)
        self.quizzes[uid] = Quiz(scope=scope, kind=kind, items=items)
        return self._question(uid)

    def _question(self, uid: int) -> Resp:
        qz = self.quizzes[uid]
        it = qz.items[qz.i]
        head = "<b>Вопрос %d/%d</b>" % (qz.i + 1, len(qz.items))
        if qz.kind in ("final", "quick"):
            head += " · урок %d" % it["unit"]
        text = head + "\n\n" + it["text"]
        stop = [("⛔ Закончить", "q:stop")]
        if it["kind"] == "choice":
            rows = [[(opt, "a:%d:%d" % (qz.i, k))] for k, opt in enumerate(it["options"])]
            return one(text, rows + [stop])
        if not qz.revealed:
            return one(text, [[("💡 Показать ответ", "g:%d:s" % qz.i)], stop])
        text += "\n\n<b>Ответ:</b> " + it["answer"]
        return one(text, [[("✅ Верно", "g:%d:y" % qz.i), ("❌ Ошибся", "g:%d:n" % qz.i)], stop])

    def _answer(self, uid: int, i: int, k: int) -> Resp:
        qz = self.quizzes.get(uid)
        if qz is None:
            return toast(STALE)
        if i != qz.i or qz.answered:
            return toast("На этот вопрос уже отвечено.")
        it = qz.items[qz.i]
        if it["kind"] != "choice" or not 0 <= k < len(it["options"]):
            return toast("Некорректный ответ.")
        qz.answered = True
        head = "<b>Вопрос %d/%d</b>" % (qz.i + 1, len(qz.items))
        if qz.kind in ("final", "quick"):
            head += " · урок %d" % it["unit"]
        correct_txt = it["options"][it["correct"]]
        if k == it["correct"]:
            qz.score += 1
            verdict = "✅ <b>Верно!</b>"
        else:
            qz.wrong.append((it["brief"], C.esc(correct_txt), it["unit"]))
            verdict = "❌ <b>Неверно.</b>\nТвой ответ: %s\nПравильно: <b>%s</b>" % (
                C.esc(it["options"][k]), C.esc(correct_txt))
        last = qz.i + 1 >= len(qz.items)
        label = "🏁 Результат" if last else "➡️ Дальше"
        return one(head + "\n\n" + it["text"] + "\n\n" + verdict, [[(label, "nx:%d" % qz.i)]])

    def _self_graded(self, uid: int, i: int, act: str) -> Resp:
        qz = self.quizzes.get(uid)
        if qz is None:
            return toast(STALE)
        if i != qz.i or qz.answered:
            return toast("На этот вопрос уже отвечено.")
        it = qz.items[qz.i]
        if it["kind"] != "self":
            return toast("Некорректное действие.")
        if act == "s":
            if qz.revealed:
                return toast("Ответ уже показан.")
            qz.revealed = True
            return self._question(uid)
        if act in ("y", "n"):
            if not qz.revealed:
                return toast("Сначала покажи ответ.")
            if act == "y":
                qz.score += 1
            else:
                qz.wrong.append((it["brief"], it["answer_plain"], it["unit"]))
            return self._advance(uid)
        raise ValueError

    def _next(self, uid: int, i: int) -> Resp:
        qz = self.quizzes.get(uid)
        if qz is None:
            return toast(STALE)
        if i != qz.i or not qz.answered:
            return toast("Это действие уже выполнено.")
        return self._advance(uid)

    def _advance(self, uid: int) -> Resp:
        qz = self.quizzes[uid]
        qz.i += 1
        qz.answered = False
        qz.revealed = False
        if qz.i >= len(qz.items):
            return self._quiz_finish(uid, qz)
        return self._question(uid)

    def _quiz_finish(self, uid: int, qz: Quiz, stopped: bool = False, pop: bool = True) -> Resp:
        if pop:
            self.quizzes.pop(uid, None)
        done = qz.score + len(qz.wrong) if stopped else len(qz.items)
        total = max(done, 1)
        pct = round(100 * qz.score / total)
        title = "⛔ Остановлено" if stopped else "🏁 Результат"
        lines = ["%s: <b>%d/%d (%d%%)</b>" % (title, qz.score, done, pct)]
        if not stopped and qz.kind == "final":
            lines.append("Норма: от 85%. " + ("✅ Пройден." if pct >= 85 else "Пока не дотягивает, разбери ошибки."))
        if qz.wrong:
            lines.append("\n<b>Ошибки:</b>")
            for brief, ans, unit in qz.wrong:
                lines.append("• %s\n   → <b>%s</b> (урок %d)" % (brief, ans, unit))
            if qz.kind in ("final", "quick"):
                units = sorted({u for _, _, u in qz.wrong})
                lines.append("\nПовтори уроки: " + ", ".join(str(u) for u in units))
        elif not stopped:
            lines.append("Без ошибок.")
        rows = [[("🔁 Ещё раз", "q:%s:%s" % (qz.scope, qz.kind))],
                [("✏️ К тестам", "m:tests")], HOME]
        return one("\n".join(lines), rows)

    # ------------------------------------------------------------ напоминания
    def _notify_menu(self, uid: int) -> Resp:
        cur = self.db.get_remind(uid)
        state = "каждый день в <b>%s</b>" % cur if cur else "<b>выключены</b>"
        text = ("⏰ <b>Напоминания</b>\n\nСейчас: %s\nЧасовой пояс: %s\n\n"
                "Выбери время или напиши команду, например: <code>/remind 19:30</code>") % (state, self.tz_name)
        times = ["07:00", "09:00", "12:00", "15:00", "18:00", "20:00", "21:00", "22:00"]
        rows = [[(t, "n:set:%s" % t.replace(":", "")) for t in times[i:i + 4]] for i in range(0, len(times), 4)]
        rows.append([("🔕 Выключить", "n:off")])
        rows.append(HOME)
        return one(text, rows)

    def _notify(self, uid: int, p: list[str]) -> Resp:
        if p[1] == "off":
            self.db.set_remind(uid, None)
            return self._notify_menu(uid)
        if p[1] == "set":
            hhmm = p[2]
            if not re.fullmatch(r"\d{4}", hhmm):
                raise ValueError
            return self._set_time(uid, int(hhmm[:2]), int(hhmm[2:]))
        raise ValueError

    def _set_time(self, uid: int, hh: int, mm: int) -> Resp:
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return one("Не понял время. Формат: <code>/remind 19:30</code>")
        self.db.set_remind(uid, "%02d:%02d" % (hh, mm))
        return self._notify_menu(uid)

    def set_reminder_from_text(self, uid: int, arg: str) -> Resp:
        """Для команды /remind. Без аргумента открывает меню напоминаний."""
        self.db.ensure_user(uid)
        arg = (arg or "").strip()
        if not arg:
            return self._notify_menu(uid)
        if arg.lower() in ("off", "выкл", "стоп"):
            self.db.set_remind(uid, None)
            return self._notify_menu(uid)
        m = TIME_RE.match(arg)
        if not m:
            return one("Не понял время. Формат: <code>/remind 19:30</code> или <code>/remind off</code>")
        return self._set_time(uid, int(m.group(1)), int(m.group(2)))

    def due_reminders(self, now: datetime) -> list[int]:
        """Кому сейчас пора отправить напоминание (окно 15 минут после выбранного времени)."""
        today = now.date().isoformat()
        mins = now.hour * 60 + now.minute
        result = []
        for row in self.db.users_with_reminders():
            hh, mm = (int(x) for x in row["remind"].split(":"))
            diff = mins - (hh * 60 + mm)
            if 0 <= diff <= 15 and row["last_sent"] != today:
                result.append(row["user_id"])
        return result

    def reminder_payload(self, uid: int) -> Resp:
        due, new = self.counts(uid)
        text = "⏰ <b>Время арабского!</b>\n📚 Часть %d\n\n🔁 К повторению: <b>%d</b>\n🆕 Новых слов: <b>%d</b>" % (
            self._part(uid).n, due, new)
        rows = [[("🃏 Карточки", "w:c:D:d")], [("🎯 Быстрый тест", "q:Q:quick")], [("🏠 Меню", "m:main")]]
        return one(text, rows)

    def start_text(self, uid: int) -> Resp:
        """Любое обращение к боту (/start, обычный текст): сначала выбор части книги."""
        self.db.ensure_user(uid)
        return self._part_choice(uid)

    def menu_text(self, uid: int) -> Resp:
        """/menu: меню выбранной части (если часть ещё не выбрана — выбор части)."""
        self.db.ensure_user(uid)
        if self.db.get_part(uid) is None:
            return self._part_choice(uid)
        return self._menu(uid, "main")
