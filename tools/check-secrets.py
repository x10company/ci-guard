# -*- coding: utf-8 -*-
"""Сторож: не пускает в коммит ключи, огромные файлы и абсолютные пути.

Зачем: однажды из репозитория пришлось выгребать живые ключи — токены сервисов,
ключи облака, внутренний JWT. Половина лежала не строкой,
а запасным значением после чтения окружения (`process.env.X || "..."`), поэтому
глазами не находилась. Без сторожа это вернётся первым же коммитом.

Запуск:
    python tools/check-secrets.py --staged     проверить то, что в индексе (хук)
    python tools/check-secrets.py --all        проверить всё дерево (CI)
    python tools/check-secrets.py FILE...      проверить конкретные файлы

Код возврата 1 — есть находки.
"""
import argparse
import io
import os
import math
import re
import subprocess
import sys
from collections import Counter

def _repo_root():
    """Корень репозитория — от git, а не «на два уровня выше себя».

    Раньше корень вычислялся из расположения файла, и стоило положить сторож не
    в tools/, как он начинал проверять соседний каталог и бодро сообщал «чисто».
    Именно так он однажды пропустил .env с живыми ключами в соседнем проекте.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", cwd=here)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except OSError:
        pass
    return os.path.dirname(here)


def _koren_iz_argumentov():
    """Корень, заданный явно: --koren <путь>.

    Нужен, когда сторож лежит НЕ внутри проверяемого репозитория. В CI его
    выкачивают отдельным шагом в подкаталог, и у того подкаталога свой .git —
    тогда git rev-parse отдаёт корень сторожа, и он проверяет сам себя,
    сообщая «чисто». Проверено опытом 21.08: код возврата 0 на репозитории,
    который вообще не смотрели.
    """
    a = sys.argv
    if "--koren" in a:
        i = a.index("--koren")
        if i + 1 < len(a):
            return os.path.abspath(a[i + 1])
    return None


REPO = _koren_iz_argumentov() or _repo_root()

SKIP_DIRS = {
    ".git", "node_modules", "bin", "obj", "__pycache__", ".venv", "venv",
    ".vs", ".idea", "dist", "build", "packages", ".playwright",
}
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz",
    ".7z", ".rar", ".exe", ".dll", ".pdb", ".so", ".dylib", ".mp4", ".avi",
    ".woff", ".woff2", ".ttf", ".eot", ".bundle", ".pyc", ".lock",
}

MAX_MB = 50          # предупреждаем заранее: жёсткий предел GitHub — 100 МБ
MAX_SCAN_MB = 12     # файлы крупнее не сканируем построчно, только по размеру

# ── что считаем ключом ────────────────────────────────────────────────────────
# Именованные, чтобы в сообщении было понятно, что именно нашли.
PATTERNS = [
    ("Airtable PAT",        re.compile(r"\bpat[A-Za-z0-9]{14}\.[a-f0-9]{64}\b")),
    ("Facebook-токен",      re.compile(r"\bEAA[A-Za-z0-9]{40,}\b")),
    ("токен Telegram-бота", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("JWT",                 re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("приватный ключ",      re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY")),
    ("AWS-ключ",            re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack-токен",         re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("ключ Anthropic",      re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("ключ OpenAI",         re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
]

# Файлы вида KEY=значение (.env и подобные). Там ключи лежат не в кавычках и не
# похожи ни на один образец: RUNWARE_API_KEY, ANTHROPIC_API_KEY, пароли SSH.
# Ловим по имени слева и длине значения справа.
ENVLINE = re.compile(
    r"""(?ix)
    ^\s*
    (?P<name> [A-Z0-9_]* (?: KEY | TOKEN | SECRET | PASSWORD | PASSWD | PWD | API ) [A-Z0-9_]* )
    \s* = \s*
    (?P<val> [^\s'"#]{16,} )
    \s*$
    """
)

# hex-ключ, но только если он присвоен переменной с говорящим именем —
# иначе ловится каждый хеш коммита и каждый md5 в дампе
HEXKEY = re.compile(
    r"""(?ix)
    \b (?: api[_-]?key | apikey | secret | token | password | passwd | pwd | auth[_-]?key )
    \b [^\n]{0,20} ['"] ([a-f0-9]{32,64}) ['"]
    """
)

# Строка «имя=значение», где имя ЛЮБОЕ, а значение выглядит случайным.
#
# Зачем отдельно от ENVLINE. То правило смотрит на имя слева и работает только
# в файлах вида .env. 19.08 мимо него прошёл ключ `sav=4iXxez…` в обычном .md:
# имя поля не содержало ни key, ни token, ни secret, а файл был README.
# Значение же было настоящим ключом — соседние строки в том же блоке кто-то
# уже заменил заглушками, а это забыл.
#
# Поэтому здесь смотрим не на имя, а на значение: длина, три класса символов
# и энтропия Шеннона. Порог 3.9 бит на символ подобран замером по всему дереву:
# 15 срабатываний, все настоящие ключи, ложных ноль.
ENTROPIYA = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9_.-]{1,40})\s*=\s*"
    r"(?P<val>[A-Za-z0-9_.~+/=-]{24,})\s*$")

# Идентификаторы Airtable — адреса, а не ключи: без токена по ним ничего не сделать.
AIRTABLE_ID = re.compile(r"^(?:app|tbl|viw|fld|rec|pgl|usr|wfl)[A-Za-z0-9]{14,17}$")


def _entropiya(s):
    """Энтропия Шеннона в битах на символ."""
    if not s:
        return 0.0
    n = len(s)
    return -sum((k / n) * math.log2(k / n) for k in Counter(s).values())


def pohozhe_na_klyuch(val):
    if AIRTABLE_ID.match(val):
        return False
    if re.fullmatch(r"[0-9a-f]+", val):      # чистый hex: хеши коммитов, md5, sha
        return False
    if re.fullmatch(r"[0-9]+", val):
        return False
    # нужен и нижний регистр, и верхний, и цифры — иначе это слово, а не ключ
    if not (re.search(r"[a-z]", val) and re.search(r"[A-Z]", val)
            and re.search(r"[0-9]", val)):
        return False
    return _entropiya(val) >= 3.9


# ── что ключом НЕ считаем ─────────────────────────────────────────────────────
ALLOW = [
    re.compile(r"<значение в X10_[A-Z0-9_]+"),      # наш указатель на менеджер паролей
    # заглушки в .env.example и подобных: your_bot_token, YOUR_API_KEY, <вставь сюда>
    re.compile(r"(?i)\b(?:xxx+|placeholder|example|sample|dummy|changeme|your[_-]\w+)\b"),
    re.compile(r"=\s*<[^>]+>\s*$"),
    re.compile(r"\b(?:0{16,}|1234567890)\b"),
    re.compile(r"(?i)process\.env\.|os\.environ|GetEnvironmentVariable|input\.secret|\$env:"),
]

# Какие каталоги считаем «внутри проекта». Абсолютный путь туда ломается при
# каждом переносе, поэтому его и ловим.
#
# Список настраивается, а не зашит: у каждого проекта свои имена, а сторож —
# один файл на все репозитории. По умолчанию берём имя корня репозитория;
# переопределить можно переменной GUARD_KATALOGI через запятую.
def _katalogi_proekta():
    """Откуда берём список: переменная окружения, файл проекта, имя корня.

    Файл `.guard-katalogi` в корне репозитория — по имени каталога на строку
    или через запятую. Он нужен, когда путей внутрь проекта несколько:
    например и рабочая папка, и каталог развёртывания.
    """
    iz_sredy = os.environ.get("GUARD_KATALOGI")
    fayl = os.path.join(REPO, ".guard-katalogi")
    if iz_sredy:
        syrye = iz_sredy
    elif os.path.isfile(fayl):
        syrye = io.open(fayl, encoding="utf-8").read()
    else:
        syrye = os.path.basename(REPO)
    # Сначала отбрасываем строки-комментарии целиком, и только потом режем по
    # запятым. В обратном порядке куски пояснений попадают в список как имена
    # каталогов — проверено, так и вышло с первой редакции.
    imena = []
    for stroka in syrye.splitlines():
        stroka = stroka.strip()
        if not stroka or stroka.startswith("#"):
            continue
        imena += [x.strip() for x in stroka.split(",") if x.strip()]
    return "|".join(re.escape(x) for x in imena)


KATALOGI_PROEKTA = _katalogi_proekta()

ABS_PATH = re.compile(
    # Ловим пути ВНУТРЬ нашего дерева: их ломает каждый перенос каталога.
    # Пути в системные установки (AppData, Program Files) machine-specific,
    # но нашими переездами не ломаются — их сюда не берём, иначе сторож
    # выдаёт десятки строк на .bat с путём к python и его выключают.
    r"""['"][a-zA-Z]:[\\/]{1,2}(?:%s)[\\/]""" % KATALOGI_PROEKTA)

# Абсолютный путь ругаем только в коде. В .json и .md он сплошь и рядом законен:
# сгенерированные индексы (facebook/data/schemas/_*.json) — это перечни файлов,
# и абсолютные пути там по смыслу. Если ругаться и на них, сторож выдаёт сотни
# строк шума, после чего его просто выключают.
ABS_PATH_EXT = {".js", ".cjs", ".mjs", ".py", ".cs", ".ps1", ".sh", ".bat", ".cmd", ".php"}

# Какие файлы считаем «env-подобными»: там ключи лежат голыми, без кавычек.
ENVLIKE = re.compile(r"(?i)(?:^|/)\.env(?:\.|$)|(?:^|/)[^/]*\.env$|\.env\.[a-z]+$")

TEMPLATE = re.compile(r"(?i)\.(?:example|sample|template|dist)$|\.(?:example|sample|template)\.")

COMMENT = re.compile(r"^\s*(?://|#|;|\*|/\*|rem\s|REM\s)")


def is_allowed(line):
    return any(a.search(line) for a in ALLOW)


def iter_files(mode, explicit):
    if explicit:
        for f in explicit:
            yield f
        return
    if mode == "staged":
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, encoding="utf-8", cwd=REPO,
        ).stdout
        for line in out.splitlines():
            if line.strip():
                yield line.strip()
        return
    # --all: только то, что git видит, — отслеживаемое плюс новое неигнорируемое.
    # Обход диска здесь не годится: он тащит игнорируемое (профили Chrome, bin, дампы)
    # и сторож тонет в шуме, из-за которого его выключают.
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO,
    ).stdout
    for line in out.splitlines():
        if line.strip():
            yield line.strip()



# ── имя используется раньше, чем импортировано ────────────────────────────────
# py_compile такое пропускает: синтаксис верен, падает только при запуске.
# Я вносил эту ошибку дважды, автоматически подставляя os.environ и вставляя
# import os после первой попавшейся строки import — а она была в конце файла.
PY_IMPORT_OS = re.compile(
    r"^\s*(?:from\s+os(?![\w])|import\s+(?:[\w.]+\s*,\s*)*os(?![\w]))", re.M)
PY_USE_OS = re.compile(r"(?<![\w.])os\.")
PY_IMPORT_IDS = re.compile(r"^\s*from\s+ids\s+import\s+IDS", re.M)
PY_USE_IDS = re.compile(r'IDS\["')
JS_IMPORT_IDS = re.compile(r"""require\(["'][^"']*config/ids["']\)""")
JS_USE_IDS = re.compile(r"ids\.airtable\.")


def check_import_order(rel, text, findings):
    ext = os.path.splitext(rel)[1].lower()
    pairs = []
    if ext == ".py":
        pairs = [(PY_IMPORT_OS, PY_USE_OS, "os"), (PY_IMPORT_IDS, PY_USE_IDS, "IDS")]
    elif ext in (".js", ".cjs"):
        pairs = [(JS_IMPORT_IDS, JS_USE_IDS, "ids")]
    for imp, use, name in pairs:
        mu = use.search(text)
        if not mu:
            continue
        mi = imp.search(text)
        if mi and mi.start() < mu.start():
            continue
        line = text[:mu.start()].count("\n") + 1
        findings.append((rel, line, "%s используется до импорта" % name,
                         "упадёт при запуске, синтаксис при этом верный"))


def check_file(rel, findings):
    full = os.path.join(REPO, rel)
    if not os.path.isfile(full):
        return
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts):
        return
    ext = os.path.splitext(rel)[1].lower()

    size_mb = os.path.getsize(full) / (1024 * 1024)
    if size_mb > MAX_MB:
        findings.append((rel, 0, "файл %.0f МБ" % size_mb,
                         "жёсткий предел GitHub — 100 МБ; данные в гит не кладём"))
        return
    if ext in SKIP_EXT or size_mb > MAX_SCAN_MB:
        return

    try:
        text = io.open(full, encoding="utf-8", errors="strict").read()
    except (UnicodeDecodeError, OSError):
        return

    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:          # минифицированный бандл — не наш код
            continue
        if is_allowed(line):
            continue
        for label, pat in PATTERNS:
            m = pat.search(line)
            if m:
                findings.append((rel, n, label, m.group(0)[:12] + "…"))
        m = HEXKEY.search(line)
        if m:
            findings.append((rel, n, "ключ в переменной с говорящим именем",
                             m.group(1)[:8] + "…"))

        # .env и подобные: KEY=длинное_значение.
        # В файлах-образцах (.env.example) строки KEY= — это их содержание, так что
        # правило по длине там даёт только шум. Настоящие ключи в образцах всё равно
        # поймаются выше, по образцам PATTERNS.
        if ENVLIKE.search(rel) and not TEMPLATE.search(rel):
            m = ENVLINE.match(line)
            if m and not m.group("val").startswith("<"):
                findings.append((rel, n, "значение в " + m.group("name"),
                                 m.group("val")[:6] + "…"))
        # Любое имя = случайное значение. Работает во всех файлах, включая .md:
        # ключ в примере конфигурации так же настоящий, как ключ в коде.
        if len(line) <= 400 and not TEMPLATE.search(rel):
            m = ENTROPIYA.match(line)
            if m and pohozhe_na_klyuch(m.group("val")):
                findings.append((rel, n, "случайное значение в " + m.group("name"),
                                 m.group("val")[:6] + "…"))

        # Путь в закомментированной строке — не опасен, это след истории.
        # Ключ в комментарии опасен ровно так же, как в коде: именно так у нас
        # пролежал живой токен. Поэтому послабление только для путей.
        if ext in ABS_PATH_EXT and ABS_PATH.search(line) and not COMMENT.match(line):
            findings.append((rel, n, "абсолютный путь",
                             "вынеси в переменную окружения или сделай относительным"))

    check_import_order(rel, text, findings)


# ── самопроверка: а страж всё ещё видит? ─────────────────────────────────────
#
# Зелёный ответ надо заслужить, и единственный способ — подложить заведомо
# плохое. Но подкладывать руками ненадёжно: 19.08 я трижды подряд сложил пробу
# неверно — у PAT дал 12 символов вместо 14, у токена Telegram 33 вместо 35 —
# и трижды сделал вывод «страж слеп». Страж был в порядке, неверны были пробы.
#
# Поэтому пробы лежат здесь, рядом с образцами, и правятся вместе с ними.
# Проба кладётся настоящим файлом в репозиторий и удаляется сразу: так
# проверяется весь путь, включая обработку расширений и список исключений.
PROBY = [
    # Каждая проба склеена из кусков: собранная строка настоящая по форме,
    # но в самом файле её нет, иначе страж ругался бы на собственный исходник.
    # Длины считаются, а не набираются: набирая руками, я ошибся четыре раза.
    ("Airtable PAT", "_proba.py",
     "TOKEN = '" + "pat" + "Q7wE2rT5yU8iO1" + "." + "9f2c4e7a1b8d0f63" * 4 + "'"),
    ("Facebook-токен", "_proba.py",
     "T = '" + "EAA" + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8cV1bN4mQ7wE" + "'"),
    ("токен Telegram-бота", "_proba.py",
     "BOT = '" + "738291046" + ":" + "AAFqWzXeCrVtByNuMiKoLpQaZsXdCfVgBhN" + "'"),
    ("AWS-ключ", "_proba.py",
     "AWS = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'"),
    ("ключ OpenAI", "_proba.py",
     "K = '" + "sk-" + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8cV1bN4mQ" + "'"),
    ("ключ Anthropic", "_proba.py",
     "K = '" + "sk-ant-" + "api03-Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8" + "'"),
    ("Slack-токен", "_proba.py",
     "S = '" + "xoxb-" + "738291046-Q7wE2rT5yU8iO1pA3sD6fG9h" + "'"),
    ("приватный ключ", "_proba.py",
     "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ("случайное значение", "_proba.md",
     "sav=" + "4iXxezjOuBENxVe3y3mdfjN2pOAXi7KDzL6qVrkZ"),
]


def samoproverka():
    """Каждый образец проверяется на им же построенной пробе."""
    print()
    print("  Самопроверка стража")
    print("  " + "-" * 56)
    provaleno = []
    # У каждой пробы своё имя. С общим именем проверка мигала: удаление файла
    # на Windows под антивирусом происходит не сразу, и следующая проба не могла
    # занять то же имя — PermissionError валил весь прогон примерно раз из шести.
    for nomer, (imya, fayl, soderzhimoe) in enumerate(PROBY, 1):
        koren_imeni, rasshirenie = os.path.splitext(fayl)
        fayl = "%s%d%s" % (koren_imeni, nomer, rasshirenie)
        polnyy = os.path.join(REPO, fayl)
        io.open(polnyy, "w", encoding="utf-8", newline="").write(soderzhimoe + chr(10))
        try:
            findings = []
            check_file(fayl, findings)
            # Пробу могло не прочитаться: файл только что создан, и его успевает
            # подхватить антивирус или редактор. Тогда findings пуст не потому,
            # что образец слеп. Отличаем одно от другого явно, иначе проверка
            # кричит зря — а кричащую зря отключают.
            chitaetsya = True
            try:
                io.open(polnyy, encoding="utf-8").read()
            except OSError:
                chitaetsya = False
        finally:
            os.remove(polnyy)
        if not chitaetsya:
            print("  %-24s проба не прочиталась, повтори запуск" % imya)
            provaleno.append(imya + " (проба не прочиталась)")
            continue
        poyman = bool(findings)
        print("  %-24s %s" % (imya, "видит" if poyman else "НЕ ВИДИТ"))
        if not poyman:
            provaleno.append(imya)
    print()
    if provaleno:
        print("  ОСТАНОВЛЕНО: страж перестал видеть %d образцов из %d:"
              % (len(provaleno), len(PROBY)))
        for imya in provaleno:
            print("      " + imya)
        return 1
    print("  все %d образцов на месте" % len(PROBY))
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--samoproverka", action="store_true")
    ap.add_argument("--koren", help="корень проверяемого репозитория")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    if a.samoproverka:
        return samoproverka()
    mode = "staged" if a.staged else "all"

    findings = []
    for rel in iter_files(mode, a.files):
        check_file(rel, findings)

    print("  сторож: корень %s, файлов %d" % (REPO, len(list(iter_files(mode, a.files)))))
    if not findings:
        print("check-secrets: чисто")
        return 0

    print("")
    print("  ОСТАНОВЛЕНО: похоже на секрет или на то, чему не место в гите")
    print("")
    cur = None
    for rel, line, label, extra in findings:
        if rel != cur:
            print("  %s" % rel)
            cur = rel
        where = ":%d" % line if line else ""
        print("      %s%s  %s  %s" % (label, where, "—", extra))
    print("")
    print("  Ключи выносим в C:\\secrets\\x10.env и читаем из окружения.")
    print("  Для Airtable-скриптов — input.secret(), окружения там нет.")
    print("  Подробности — в документе проекта о хранении секретов.")
    print("")
    print("  Если это ложное срабатывание — допиши правило в ALLOW")
    print("  в tools/check-secrets.py и объясни в коммите, почему безопасно.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
