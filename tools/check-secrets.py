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

# «bin» здесь НЕТ намеренно. В проектах .NET это выхлоп, а в проектах на
# скриптах — каталог с КОДОМ: у трёх наших направлений весь продукт лежит именно
# в bin/, и сторож их не проверял вовсе. Найдено 07.09.2026.
#
# Та же ошибка была в .gitignore набора и там уже исправлена — а здесь осталась.
# Одно знание («bin — это выхлоп») жило в двух местах, починили одно.
# Выхлоп сборки отсекается ниже, по SKIP_PUTI, парой каталогов.
SKIP_DIRS = {
    ".git", "node_modules", "obj", "__pycache__", ".venv", "venv",
    ".vs", ".idea", "dist", "build", "packages", ".playwright",
    # _probe — сохранённые страницы чужих сайтов из проб: их токены не наши,
    # а разбирать их сторожу нечем, кроме шума.
    "_probe",
    # Профили браузеров: там куки и состояние сеансов. Это рабочие данные
    # запущенных программ, а не наш код — сторожу там делать нечего.
    "chromium-profile", "chrome-profile", "chrome_profile", "chrome-data",
}

# Пропускаем не по имени каталога, а по паре: так «bin» с кодом остаётся под
# проверкой, а выхлоп сборки — нет.
SKIP_PUTI = ("bin/Debug", "bin/Release", "bin/x64", "bin/x86")


def propustit_put(rel):
    """Отсечь выхлоп сборки, не отсекая каталоги кода с тем же именем."""
    put = rel.replace("\\", "/")
    return any(("/" + kus + "/") in ("/" + put) or put.startswith(kus + "/")
               for kus in SKIP_PUTI)
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

# Ключ в кавычках: NAME = "значение".
#
# Зачем отдельно от ENVLINE: то правило требует значение БЕЗ кавычек и работает
# только в .env-подобных файлах. Из-за этого 22.08.2026 страж не видел пароль
# root в BackUps/Leads/panel-tunnel.py — там было `PWD = "..."`, то есть имя
# говорящее, а форма записи не совпала ни с одним образцом. Он же лежал в
# Он же лежал в .env соседнего проекта и в документе по серверам — и все три
# раза страж молчал.
KAVYCHKI = re.compile(r"""(?ix)
    \b (?P<name> [A-Za-z0-9_]* (?: KEY | TOKEN | SECRET | PASSWORD | PASSWD | PWD | PASS )
                 [A-Za-z0-9_]* )
    (?:
        # Необязательная кавычка перед двоеточием — это json: "SecretKey": "…".
        # Без неё правило не видело ключи в appsettings.json, а файл
        # лежит в git. Нашлось 23.08.2026, когда разбирали остаток находок.
        ['"]? \s* [:=] \s*
      |
        # Пара через запятую: инициализатор словаря `{ "api_token", "…" }`.
        # Так лежал живой токен сразу в четырёх файлах — и страж
        # отвечал «чисто», потому что между именем и значением стоит запятая,
        # а не знак равенства. Найдено 24.08.2026, третий случай того же класса.
        #
        # Кавычка перед запятой ОБЯЗАТЕЛЬНА: имя должно быть строкой-литералом.
        # Без неё правило ловило бы каждый вызов вида
        # `Send(botToken, "текст сообщения длиннее шестнадцати символов")`.
        ['"] \s* , \s*
    )
    (?P<q>['"]) (?P<val> [^'"\n]{16,} ) (?P=q)
    """)

# Запасное значение после чтения окружения: getenv("NAME", "ключ").
#
# Ровно тот случай, из-за которого сторож и заводился: ключ лежит не строкой,
# а вторым аргументом, и глазами не находится. Прежние правила его не видели —
# между именем и значением стоит запятая, а не знак равенства. Найдено 23.08.2026
# в соседнем проекте: getenv("…_API_KEY", "<живой ключ>") сразу в двух файлах.
ZAPASNOYE = re.compile(r"""(?ix)
    (?: getenv | environ\.get ) \s* \( \s*
    ['"] (?P<name> [A-Za-z0-9_]* (?: KEY | TOKEN | SECRET | PASSWORD | PASSWD | PWD )
                   [A-Za-z0-9_]* ) ['"]
    \s* , \s* ['"] (?P<val> [^'"\n]{16,} ) ['"]
    """)

# Подпись по-русски: «Пароль ключа: значение», в том числе в markdown с `**`.
# Ни ENVLINE, ни ENTROPIYA такую строку не видят — там нет знака `=`.
# Значение после подписи не должно выглядеть кодом или заглушкой: в прозе
# и в markdown после «ключ:» чаще стоит выражение (`name.match(/.../)`),
# фрагмент лога или пример вида `123456789:ABCdef...`, а не сам секрет.
KOD_ILI_ZAGLUSHKA = re.compile("[" + re.escape("{}()[]$<>|/" + chr(92) + chr(96)) + "]" + "|[.]{3}|" + chr(8230))

PODPIS_RU = re.compile(
    r"(?i)(?:парол|ключ|токен|секрет)[^:\n]{0,24}:\s*\**\s*(?P<val>[^\s*][^\s]{15,})")

# Файл, который целиком является секретом: имя намекает, внутри одна строка.
# Так у нас лежал BackUps/ERP/.ssh_password — ни имени переменной, ни `=`,
# зацепиться не за что, и страж честно отвечал «чисто».
IMYA_SEKRETA = re.compile(r"(?i)(?:^|/)[^/]*(?:password|passwd|secret|\.key|\.pem)[^/]*$")

# Идентификаторы Airtable — адреса, а не ключи: без токена по ним ничего не сделать.
AIRTABLE_ID = re.compile(r"^(?:app|tbl|viw|fld|rec|pgl|usr|wfl)[A-Za-z0-9]{14,17}$")


def _entropiya(s):
    """Энтропия Шеннона в битах на символ."""
    if not s:
        return 0.0
    n = len(s)
    return -sum((k / n) * math.log2(k / n) for k in Counter(s).values())


def pohozhe_na_klyuch(val):
    # Обрамление снимаем до проверок. Иначе `fldXXXXXXXXXXXXXX` с прилипшей
    # кавычкой перестаёт совпадать с AIRTABLE_ID и уходит в находки как ключ.
    val = val.strip("'\"`«»()[]{}<>,.;:*")
    if len(val) < 16:
        return False
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
    # Заглушки из подряд идущего алфавита: patABCDEFGHIJ..., ABCdef123.
    re.compile(r"(?i)abcdef|defghi|klmnop"),
    # Заглушки из подряд идущих цифр вперемешку с abc: patABCD1234567890.xyz123abc.
    re.compile(r"(?i)abcd1234|1234567890|xyz123"),
    re.compile(r"(?i)process\.env\.|os\.environ|GetEnvironmentVariable|input\.secret|\$env:"),
    # Отпечаток открытого ключа — не секрет: он для сверки и печатается ssh-keygen.
    re.compile(r"SHA256:[A-Za-z0-9+/=]{20,}"),
    # Открытый ключ в DER/base64 (начинается на MII) — публичная часть, не секрет.
    # Так лежит ключ расширения Chrome в manifest.json: он там обязателен и виден всем.
    re.compile(r"MII[A-Za-z0-9+/=]{20,}"),
    # Ссылка на переменную окружения, а не значение: "${X10_FOO}", "$FOO",
    # "%X10_FOO%". Оболочка и .NET подставят их при запуске.
    re.compile(r"[$][{]?[A-Za-z_][A-Za-z0-9_]*[}]?|%[A-Z][A-Z0-9_]+%"),
    # Явные выдумки из документации: MySecurePassword, SuperSecret123 и подобное.
    re.compile(r"(?i)(?:my|super|strong|test)(?:secure|secret|safe)?(?:password|pass|token)\w*"),
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

# Расширения, которые смотрим в игнорируемых файлах: конфиги и выгрузки.
# Крупные файлы обход и так пропускает по размеру.
DANNYE_EXT = {".json", ".txt", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".xml"}

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


KLYUCH_ZAGOLOVOK = "-----BEGIN "


def klyuchi_v_dereve(findings):
    """Приватные ключи ищем обходом диска, а не через git.

    Почему отдельно от всего остального. Список файлов страж берёт у git —
    отслеживаемое плюс новое неигнорируемое. Это верно для коммита, но у него
    есть слепое пятно: то, что в .gitignore, для git не существует, значит и для
    стража не существует.

    Именно так три копии ssh-ключа пролежали в рабочем дереве с 17.08 по
    22.08.2026: строка `*_key` в .gitignore закрыла их от коммита в первый же
    день — и в тот же день сделала невидимыми для проверки. В коммит они не
    уедут, а вот в архив, в копию каталога или в чужие руки вместе с папкой —
    уедут. Ключ должен лежать в C:\secrets, а в репозитории не лежать вовсе.

    Поэтому здесь ходим по диску и смотрим только на первую строку файла:
    заголовок приватного ключа. Правило намеренно узкое — библиотеки, где слово
    PRIVATE KEY встречается в коде, первой строкой его не начинают.
    """
    envy = []
    for koren, katalogi, fayly in os.walk(REPO):
        katalogi[:] = [k for k in katalogi if k not in SKIP_DIRS]
        if propustit_put(os.path.relpath(koren, REPO)):
            continue
        for imya in fayly:
            polnyy = os.path.join(koren, imya)
            try:
                if os.path.getsize(polnyy) > 20000:
                    continue
                with io.open(polnyy, encoding="utf-8", errors="strict") as f:
                    pervaya = f.readline(80)
            except (OSError, UnicodeDecodeError):
                continue
            if pervaya.startswith(KLYUCH_ZAGOLOVOK) and "PRIVATE KEY" in pervaya:
                rel = os.path.relpath(polnyy, REPO).replace("\\", "/")
                findings.append((rel, 1, "приватный ключ лежит в дереве",
                                 "даже в .gitignore он остаётся на диске: место ключа — C:\secrets"))
                continue
            rel = os.path.relpath(polnyy, REPO).replace("\\", "/")
            if TEMPLATE.search(rel):
                continue
            # Кроме .env берём конфиги и выгрузки данных: 23.08.2026 в игноре
            # нашлись json с паролями к прокси, по одному на запись. Расширение
            # ограничиваем нарочно — иначе в обход попадёт всё дерево.
            if ENVLIKE.search(rel) or os.path.splitext(rel)[1].lower() in DANNYE_EXT:
                envy.append((rel, polnyy))

    # Секреты в .env, которые скрыты игнором. Правило про KEY=значение у стража
    # есть с самого начала, но игнорируемые файлы он не открывает — и три .env
    # из перенесённых проектов соседних проектов
    # так и жили со своими копиями ключей. Проверяем ровно то, что игнор скрыл:
    # отслеживаемые .env и без этого проходят обычным путём.
    if not envy:
        return
    skrytye = _ignoriruyemye([rel for rel, _ in envy])
    for rel, polnyy in envy:
        if rel not in skrytye:
            continue
        try:
            text = io.open(polnyy, encoding="utf-8", errors="strict").read()
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            # По образцам тоже: ключ Anthropic или PAT Airtable в скрытом .env
            # правило «имя=длинное значение» пропускает, если энтропия ниже порога.
            for label, pat in PATTERNS:
                if pat.search(line):
                    findings.append((rel, n, label + " в файле, скрытом игнором",
                                     "значение должно приходить из X10_*"))
                    break
            m = ENVLINE.match(line)
            if m and pohozhe_na_klyuch(m.group("val")):
                findings.append((rel, n, "секрет в файле, скрытом игнором",
                                 "своя копия ключа: значение должно приходить из X10_*"))


def _ignoriruyemye(otnositelnye):
    """Какие из путей git считает игнорируемыми. Один вызов на весь список."""
    if not otnositelnye:
        return set()
    # Пути отдаём БАЙТАМИ, а не текстом. В текстовом режиме subprocess на Windows
    # переводит перевод строки в CRLF, и git получает путь с прилипшим CR —
    # такого файла нет, значит «не игнорируется». Проверено 23.08.2026: из трёх
    # путей возвращался ровно последний — тот, к которому CR не приклеился.
    stdin = (chr(10).join(otnositelnye) + chr(10)).encode("utf-8")
    out = subprocess.run(["git", "check-ignore", "--stdin"],
                         input=stdin, capture_output=True, cwd=REPO)
    stdout = out.stdout.decode("utf-8", errors="replace")
    return {x.strip().replace("\\", "/") for x in stdout.splitlines() if x.strip()}


def check_file(rel, findings):
    full = os.path.join(REPO, rel)
    if not os.path.isfile(full):
        return
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts) or propustit_put(rel):
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

    if IMYA_SEKRETA.search(rel.replace("\\", "/")) and len(text) <= 400:
        stroki = [x.strip() for x in text.splitlines() if x.strip()]
        if len(stroki) == 1 and pohozhe_na_klyuch(stroki[0]):
            findings.append((rel, 1, "файл целиком — секрет",
                             stroki[0][:6] + "…"))

    # Свой же исходник по правилам про форму записи не проверяем: в нём лежат
    # определения образцов, а не значения.
    svoy = rel.replace("\\", "/").endswith("tools/check-secrets.py")

    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:          # минифицированный бандл — не наш код
            continue

        # Запасное значение проверяем ДО списка исключений, и вот почему.
        # В ALLOW есть строка про os.environ и process.env — она гасит шум на
        # коде, который читает окружение. Но именно на таких строках и живёт
        # опасный случай: getenv("KEY", "живой ключ"). Проверь его после ALLOW —
        # и правило не сработает никогда.

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
        m = None if svoy else KAVYCHKI.search(line)
        # `cachekey`, `sortkey` и подобные содержат KEY, но секретом не являются.
        if m and not re.search(r"(?i)cache|sort|idempot", m.group("name")) and pohozhe_na_klyuch(m.group("val")):
            findings.append((rel, n, "значение в кавычках у " + m.group("name"),
                             m.group("val")[:6] + "…"))
        m = None if svoy else PODPIS_RU.search(line)
        if m and not KOD_ILI_ZAGLUSHKA.search(m.group("val")) and pohozhe_na_klyuch(m.group("val")):
            findings.append((rel, n, "секрет после русской подписи",
                             m.group("val")[:6] + "…"))
        m = None if svoy else ZAPASNOYE.search(line)
        if m and pohozhe_na_klyuch(m.group("val")):
            findings.append((rel, n, "запасное значение у " + m.group("name"),
                             m.group("val")[:6] + "…"))

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
    ("значение в кавычках", "_proba.py",
     'PWD = "' + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8" + '"'),
    # Инициализатор словаря C#: имя и значение через запятую. Расширение
    # 24.08.2026, после случая с токеном в инициализаторе словаря.
    ("пара через запятую", "_proba.cs",
     '{ "api_' + 'token", "' + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8" + '" },'),
    ("секрет после подписи", "_proba.md",
     "- **" + chr(1055) + "ароль ключа:** " + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8"),
    ("файл целиком секрет", "_proba_password.txt",
     "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8"),
    ("запасное значение", "_proba.py",
     'K = os.getenv("RUNWARE_API_KEY", "' + "Q7wE2rT5yU8iO1pA3sD6fG9hJ2kL5zX8" + '")'),
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
    if mode == "all" and not a.files:
        klyuchi_v_dereve(findings)

    skolko = len(list(iter_files(mode, a.files)))
    print("  сторож: корень %s, файлов %d" % (REPO, skolko))

    # Ноль файлов — это «ничего не проверено», а не «всё хорошо». Свод proverki
    # про это и написан, а сторож сам нарушал правило: 05.09.2026 он ответил
    # «чисто» на каталоге в облачном диске, не прочитав ни одного файла.
    if skolko == 0:
        print("")
        print("  ОСТАНОВЛЕНО: не прочитано ни одного файла — проверять было нечего.")
        print("  Это не «чисто». Причины бывают такие:")
        print("    - каталог не репозиторий, а ключ --all не передан;")
        print("    - путь не существует или недоступен;")
        print("    - всё содержимое отсечено игнором.")
        print("")
        return 1

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
