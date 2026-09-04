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

# откуда|куда|зачем
NABOR="
guard.yml|.github/workflows/guard.yml|проход CI: ключи, gitleaks, синтаксис
settings.json|.claude/settings.json|своды правил подключаются при открытии
gitleaks.toml|.gitleaks.toml|исключения поиска ключей, свои у проекта
gitignore|.gitignore|без него в коммит уедет __pycache__
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

while IFS='|' read -r ottuda kuda zachem; do
  [ -z "$ottuda" ] && continue

  katalog=$(dirname "$kuda")
  [ "$katalog" != "." ] && mkdir -p "$katalog"

  if [ -e "$kuda" ] && [ "$PEREZAPISAT" -eq 0 ]; then
    printf '    - %-32s уже есть, пропускаю\n' "$kuda"
    propushcheno=$((propushcheno + 1))
    continue
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
  echo "  Нужно заменить — скачай скрипт и запусти с --perezapisat."
fi

# Ловушка, на которой уже обожглись в ApiX10: guard.yml приехал с веткой main,
# а репозиторий жил на master — проход не запустился ни разу и молчал.
vetka=$(git symbolic-ref --short HEAD 2>/dev/null || true)
if [ -n "$vetka" ] && [ "$vetka" != "main" ]; then
  echo
  echo "  ВНИМАНИЕ: ветка «$vetka», а guard.yml запускается на main."
  echo "  Поправь branches в .github/workflows/guard.yml, иначе проход"
  echo "  не запустится ни разу и об этом никто не узнает."
fi

echo
echo "  Дальше: закоммить эти файлы. После пуша проход CI заработает сам,"
echo "  а открывший репозиторий получит своды правил без установки."
echo
