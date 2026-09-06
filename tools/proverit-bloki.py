#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверяет командные блоки в документах: разбираются ли они оболочкой.

Зачем. Документ, предлагающий команду, — исполняемая часть системы: команду из
него копируют и запускают. Неверная команда в документе работает так же, как
неверная строка в коде, разница лишь в том, что исполняет её человек, и он же
считает, что проверять тут нечего.

06.09.2026 склейка прозы с последней строкой блока дала `syntax error` при
копировании — и нашлась только потому, что кто-то догадался скопировать.

ЧТО ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ. Только РАЗБОР (`bash -n`, `zsh -n`), никакого
запуска. Запускать командные блоки из документов автоматически нельзя: это
исполнение текста, который правит кто угодно, а в блоке бывает и `rm -rf`.
Проверка на то, что команда делает то, что обещано рядом, остаётся за автором —
он выполняет её сам, из файла, и вставляет настоящий вывод.

Поэтому проход ловит один класс из трёх: сломанный синтаксис и склейку. Команду,
которая разбирается, но ищет не там, он не поймает и не притворяется, что ловит.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

# Блоки, помеченные оболочкой. Прочие языки не наше дело.
OBOLOCHKI = {
    "bash": ["bash", "-n"],
    "sh": ["bash", "-n"],
    "shell": ["bash", "-n"],
    "zsh": ["zsh", "-n"],
}

# Заполнитель вида <фикстура>, <номер>, <путь>: это образец вызова, а не команда.
# Разбирать его бессмысленно — оболочка увидит перенаправление ввода-вывода.
ZAPOLNITEL = re.compile(r"<[^<>\s][^<>]*>")

BLOK = re.compile(r"^([ \t]*)```([A-Za-z0-9_+-]*)[ \t]*$")


def bloki_fayla(put):
    """Вернуть [(язык, номер первой строки, текст)] для всех блоков файла."""
    try:
        with open(put, "r", encoding="utf-8", errors="replace") as f:
            stroki = f.read().split("\n")
    except OSError:
        return []

    naydeno = []
    i = 0
    while i < len(stroki):
        m = BLOK.match(stroki[i])
        if not m:
            i += 1
            continue
        otstup, yazyk = m.group(1), m.group(2).lower()
        # Ищем закрывающий забор с тем же отступом.
        j = i + 1
        telo = []
        while j < len(stroki):
            zakryt = BLOK.match(stroki[j])
            if zakryt and zakryt.group(2) == "" and zakryt.group(1) == otstup:
                break
            telo.append(stroki[j][len(otstup):] if stroki[j].startswith(otstup) else stroki[j])
            j += 1
        if j < len(stroki) and yazyk in OBOLOCHKI:
            naydeno.append((yazyk, i + 1, "\n".join(telo)))
        i = j + 1
    return naydeno


def nayti_oboloczhku(imya):
    """Вернуть путь к рабочей оболочке или None.

    Имени недостаточно. На Windows `bash` в PATH указывает на заглушку WSL: она
    запускается, печатает «нет установленных дистрибутивов» в UTF-16 и отдаёт
    код -1. Проверка «команда нашлась» такую оболочку признаёт годной, и дальше
    проход молча пропускает ВСЕ блоки, сообщая «чисто».

    Поэтому не спрашиваем, есть ли имя, а запускаем и требуем заданный вывод.
    """
    kandidaty = []
    nayden = shutil.which(imya)
    if nayden:
        kandidaty.append(nayden)
    kandidaty += [
        os.path.join("C:" + os.sep, "Program Files", "Git", "bin", imya + ".exe"),
        os.path.join("C:" + os.sep, "Program Files", "Git", "usr", "bin", imya + ".exe"),
        "/bin/" + imya,
        "/usr/bin/" + imya,
    ]
    for put in kandidaty:
        if put != nayden and not os.path.exists(put):
            continue
        try:
            r = subprocess.run([put, "-c", "echo proba-ok"],
                               capture_output=True, timeout=20)
            if r.returncode == 0 and b"proba-ok" in r.stdout:
                return put
        except Exception:
            continue
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--koren", default=".", help="корень обхода")
    p.add_argument("--samoproverka", action="store_true",
                   help="подложить заведомо сломанный блок и убедиться, что ловится")
    a = p.parse_args()

    if a.samoproverka:
        # Зелёный результат надо заслужить: сначала спрашиваем, видит ли он вообще.
        put_ob = nayti_oboloczhku("bash")
        if not put_ob:
            print("  САМОПРОВЕРКА ПРОВАЛЕНА: рабочей bash не нашлось")
            return 1
        # Проба собрана НЕ на глаз: первая версия — `if [ -z "$X" ; then ... fi` —
        # разбирается успешно, потому что `[` без `]` ломается при исполнении, а
        # не при разборе. Годятся только незакрытые конструкции.
        proba = 'if true; then echo net\n'
        r = subprocess.run([put_ob, "-n"], input=proba, text=True,
                           capture_output=True)
        if r.returncode == 0:
            print("  САМОПРОВЕРКА ПРОВАЛЕНА: сломанный блок признан годным")
            return 1
        print("  самопроверка: сломанный блок пойман, код %d" % r.returncode)

    koren = os.path.abspath(a.koren)
    dostupno = {}
    for y in sorted(set(OBOLOCHKI)):
        dostupno[y] = nayti_oboloczhku(OBOLOCHKI[y][0])
    for y in sorted(dostupno):
        print("  оболочка %-6s %s" % (y, dostupno[y] or "не найдена, блоки пропускаю"))

    # Список файлов берём у git, а не обходом. Обход по монорепо дал 2143 блока,
    # из них почти все — чужие вендоренные деревья (.playwright в bin/Debug), и
    # три «находки» из пяти были в чужой документации. git знает, что наше:
    # выхлоп сборки и вендоренное он не отслеживает.
    fayly = []
    v_git = subprocess.run(["git", "-C", koren, "rev-parse", "--show-toplevel"],
                           capture_output=True)
    if v_git.returncode == 0:
        r = subprocess.run(["git", "-C", koren, "ls-files", "-z", "--", "*.md"],
                           capture_output=True)
        for kus in r.stdout.split(b"\0"):
            if kus:
                fayly.append(os.path.join(koren, kus.decode("utf-8", "replace")))
        print("  список файлов: git ls-files, %d документов" % len(fayly))
    else:
        for koren_p, katalogi, imena in os.walk(koren):
            katalogi[:] = [k for k in katalogi
                           if k not in (".git", "node_modules", "__pycache__", ".venv")]
            for imya in imena:
                if imya.lower().endswith(".md"):
                    fayly.append(os.path.join(koren_p, imya))
        print("  список файлов: обход каталога (не репозиторий), %d документов"
              % len(fayly))

    vsego = propushcheno = 0
    plohie = []
    for f in sorted(fayly):
        for yazyk, stroka, telo in bloki_fayla(f):
            if not telo.strip():
                continue
            if ZAPOLNITEL.search(telo):
                propushcheno += 1
                continue
            put_ob = dostupno.get(yazyk)
            if not put_ob:
                propushcheno += 1
                continue
            vsego += 1
            # Байтами, а не text=True: при text=True питон кодирует ввод
            # локальной кодировкой, и блок со стрелкой «→» роняет проход
            # с UnicodeEncodeError. Документы у нас на русском, стрелки в них
            # обычное дело.
            r = subprocess.run([put_ob] + OBOLOCHKI[yazyk][1:],
                               input=(telo + "\n").encode("utf-8"),
                               capture_output=True)
            if r.returncode != 0:
                oshibka = (r.stderr or b"").decode("utf-8", "replace").strip().split("\n")[0]
                plohie.append((os.path.relpath(f, koren), stroka, yazyk, oshibka))

    print("  блоки: корень %s, проверено %d, пропущено %d (образцы и чужие оболочки)"
          % (koren, vsego, propushcheno))

    # Ноль проверенных — это «ничего не проверено», а не «всё хорошо».
    if vsego == 0:
        print("")
        print("  ОСТАНОВЛЕНО: не проверено ни одного блока.")
        print("  Либо документов нет, либо у блоков не проставлен язык (```bash).")
        return 1

    if not plohie:
        print("proverit-bloki: чисто")
        return 0

    print("")
    print("  ОСТАНОВЛЕНО: команда из документа не разбирается оболочкой")
    print("")
    for f, stroka, yazyk, oshibka in plohie:
        print("  %s:%d  (```%s)" % (f, stroka, yazyk))
        print("      %s" % oshibka)
    print("")
    print("  Скопируйте блок и выполните сами: то, что не разбирается,")
    print("  не выполнится и у читателя. Частая причина — проза, приклеившаяся")
    print("  к последней строке блока, и незакрытая кавычка.")
    print("")
    return 1


if __name__ == "__main__":
    sys.exit(main())
