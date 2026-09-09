#!/usr/bin/env bash
# Раскладывает в текущий репозиторий четыре файла: проход CI, своды правил,
# исключения gitleaks и .gitignore.
#
# Запуск из корня нового репозитория, одной строкой:
#   curl -sSL https://raw.githubusercontent.com/x10company/ci-guard/main/primer/postavit.sh | bash
#
# Существующие файлы не трогает — сообщает и пропускает. Чтобы перезаписать,
# скачай скрипт и запусти с --perezapisat.
#
# Близнец postavit.ps1 для мака и линукса. Правишь один — правь второй.

set -u

BAZA="https://raw.githubusercontent.com/x10company/ci-guard/main/primer"
PEREZAPISAT=0
[ "${1:-}" = "--perezapisat" ] && PEREZAPISAT=1

# откуда|куда|зачем|беречь
# «беречь» = не перезаписывать даже с --perezapisat. Такой файл у проекта свой,
# и подмена его нашим может открыть то, что он закрывал: 04.09.2026 перезапись
# .gitignore открыла пять фикстур с настоящими ключами подписки.
NABOR="
guard.yml|.github/workflows/guard.yml|проход CI: ключи, gitleaks, синтаксис|net
settings.json|.claude/settings.json|регистрирует маркетплейс со сводами|net
gitleaks.toml|.gitleaks.toml|исключения поиска ключей, свои у проекта|da
gitignore|.gitignore|без него в коммит уедет __pycache__|da
"

if [ ! -d .git ]; then
  echo "  Это не корень репозитория: каталога .git рядом нет."
  echo "  Перейди в корень нового репозитория и повтори."
  exit 1
fi

echo
echo "  Раскладываю набор в $(pwd)"
echo

polozheno=0
propushcheno=0

while IFS='|' read -r ottuda kuda zachem berech; do
  [ -z "$ottuda" ] && continue

  katalog=$(dirname "$kuda")
  [ "$katalog" != "." ] && mkdir -p "$katalog"

  if [ -e "$kuda" ] && [ "$berech" = "da" ]; then
    printf '    - %-32s уже есть, НЕ трогаю (свой у проекта)\n' "$kuda"
    propushcheno=$((propushcheno + 1))
    continue
  fi

  if [ -e "$kuda" ] && [ "$PEREZAPISAT" -eq 0 ]; then
    printf '    - %-32s уже есть, пропускаю\n' "$kuda"
    propushcheno=$((propushcheno + 1))
    continue
  fi

  # Перезаписываем только с резервной копией: вернуть прежний файл должно быть
  # можно без git — набор кладут и в репозиторий с грязным деревом.
  if [ -e "$kuda" ]; then
    rezerv="$kuda.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$kuda" "$rezerv"
    printf '      прежний сохранён: %s\n' "$rezerv"
  fi

  # Во временный файл, а не сразу на место: оборванная закачка иначе оставит
  # обрезанный guard.yml, и проход CI молча не запустится.
  vremenny="$kuda.zakachka"
  if curl -sSLf -o "$vremenny" "$BAZA/$ottuda" && [ -s "$vremenny" ]; then
    mv "$vremenny" "$kuda"
    printf '    + %-32s %s\n' "$kuda" "$zachem"
    polozheno=$((polozheno + 1))
  else
    rm -f "$vremenny"
    printf '    ! %-32s не скачался\n' "$kuda"
  fi
done <<VVOD
$NABOR
VVOD

echo
echo "  Положено: $polozheno, пропущено: $propushcheno"

if [ "$propushcheno" -gt 0 ]; then
  echo "  Пропущенные не перезаписаны намеренно: у проекта могут быть свои."
  echo "  .gitignore и .gitleaks.toml не заменяются даже с --perezapisat:"
  echo "  подмена своего файла нашим может открыть то, что он закрывал."
  echo "  Нужно сравнить — открой primer/ в ci-guard и перенеси нужное руками."
fi

# Ловушка, на которой уже обожглись: guard.yml приехал с веткой main,
# а репозиторий жил на master — проход не запустился ни разу и молчал.
vetka=$(git symbolic-ref --short HEAD 2>/dev/null || true)
if [ -n "$vetka" ] && [ "$vetka" != "main" ]; then
  echo
  echo "  ВНИМАНИЕ: ветка «$vetka», а push-триггер guard.yml стоит на main."
  echo "  На pull request проход всё равно пойдёт: фильтра ветки там нет."
  echo "  Но прямые коммиты в «$vetka» проверяться не будут. Работаете без"
  echo "  pull request — поправьте branches, иначе проход не пойдёт ни разу."
fi

echo
echo "  Дальше: закоммить эти файлы. После пуша проход CI заработает сам."
echo "  Своды правил ставятся один раз на машину, не на репозиторий:"
echo "    claude plugin install x10-obshchie@x10company"
echo
