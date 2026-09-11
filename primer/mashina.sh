#!/usr/bin/env bash
# Разовая настройка машины под работу с репозиториями x10company. Мак и линукс.
# Близнец mashina.ps1 для Windows — держать в согласии.
#
# Зачем отдельно от postavit.sh: тот обвешивает ОДИН репозиторий, а этот
# настраивает машину целиком — подпись коммитов, доступ к GitHub и своды правил.
#
# Запускать можно сколько угодно раз: сделанное пропускается, своды обновляются.
# Им же и проверяют, что всё свежее, — отдельной команды помнить не надо.
#
#   bash mashina.sh [каталог-с-направлениями]
#
# По умолчанию каталог — ~/Claude Code.
set -u

KORNI="${1:-$HOME/Claude Code}"
BEDA=0

shag() { printf '\n[%s] %s\n' "$1" "$2"; }
est()  { command -v "$1" >/dev/null 2>&1; }

# --- 1. Что должно быть до нас ----------------------------------------------
shag 1 "Проверяю, что установлено"
for c in git claude; do
  if est "$c"; then printf '    %-7s есть: %s\n' "$c" "$(command -v "$c")"
  else printf '    %-7s НЕТ — поставить и запустить заново\n' "$c"; BEDA=1; fi
done
if est gh; then printf '    %-7s есть: %s\n' "gh" "$(command -v gh)"
else printf '    %-7s нет — перечня репозиториев в конце не будет\n' "gh"; fi
[ "$BEDA" -eq 1 ] && exit 1

# --- 2. Подпись коммитов -----------------------------------------------------
# Адрес именно этот. Личную почту в коммиты не кладём, она уходит в историю.
# Адрес вида <id>+<логин>@users.noreply.github.com площадка привязывает к
# учётной записи, и коммит считается своим.
shag 2 "Подпись коммитов"
git config --global user.name  "Ivan Chernyshuk"
git config --global user.email "306788272+ivanchernyshuk@users.noreply.github.com"
printf '    user.name  = %s\n' "$(git config --global user.name)"
printf '    user.email = %s\n' "$(git config --global user.email)"

# --- 3. Доступ к GitHub ------------------------------------------------------
# По HTTPS. Ключ SSH не нужен и не заводится: на канонической машине его нет.
shag 3 "Доступ к GitHub"
case "$(uname -s)" in
  Darwin)                 pomoshchnik=osxkeychain ;;
  MINGW*|MSYS*|CYGWIN*)   pomoshchnik=manager ;;   # Windows: менеджер учётных данных
  *)                      pomoshchnik=cache ;;     # линукс: держим в памяти, а не в файле
esac
# `store` не ставим сознательно: он кладёт доступ открытым текстом в ~/.git-credentials.
git config --global credential.helper "$pomoshchnik"
printf '    credential.helper = %s\n' "$(git config --global credential.helper)"
if est gh && gh auth status >/dev/null 2>&1; then
  printf '    gh: вход выполнен\n'
else
  printf '    вход в GitHub спросят при первом обращении к приватному репозиторию\n'
  printf '    либо заранее: gh auth login\n'
fi

# --- 4. Своды правил ---------------------------------------------------------
# Маркетплейс приватный, поэтому шаг 3 обязан идти раньше.
shag 4 "Своды правил"
if claude plugin marketplace list 2>&1 | grep -q x10company; then
  echo "    маркетплейс подключён, обновляю"
  claude plugin marketplace update x10company 2>&1 | sed 's/^/    /'
else
  echo "    подключаю маркетплейс x10company/claude-skills"
  claude plugin marketplace add x10company/claude-skills 2>&1 | sed 's/^/    /'
fi
for p in x10-obshchie x10-hozyaystvo; do
  vyvod=$(claude plugin update "$p@x10company" 2>&1)
  case "$vyvod" in
    *"not installed"*|*"No plugin"*|*"не установлен"*)
      echo "    ставлю $p"
      claude plugin install "$p@x10company" 2>&1 | sed 's/^/    /' ;;
    *) printf '%s\n' "$vyvod" | sed 's/^/    /' ;;
  esac
done

# --- 5. Проверка -------------------------------------------------------------
shag 5 "Что получилось"
claude plugin list 2>&1 | sed 's/^/    /'
cat <<'KONEC'

    Должно быть по ОДНОЙ строке на плагин, обе scope: user.
    Больше одной — плагин включён ещё и файлом проекта, а это замораживает
    версию: claude plugin update такую копию не двигает.
KONEC

# --- 6. Каких репозиториев тут нет -------------------------------------------
# Репозиторий не появляется на машине сам. Никакой синхронизации нет: заведённый
# на площадке приезжает только тем, что его забрали.
shag 6 "Репозитории организации против того, что лежит здесь"
if est gh && gh auth status >/dev/null 2>&1; then
  mkdir -p "$KORNI"
  nety=0
  while IFS= read -r imya; do
    [ -z "$imya" ] && continue
    if [ -d "$KORNI/$imya/.git" ]; then
      printf '    есть   %s\n' "$imya"
    else
      printf '    НЕТ    %s\n' "$imya"
      nety=$((nety + 1))
    fi
  done <<< "$(gh repo list x10company --limit 100 --json name --jq '.[].name' 2>/dev/null | sort)"

  if [ "$nety" -gt 0 ]; then
    printf '\n    Забрать недостающие (%s шт.):\n\n' "$nety"
    printf '      cd %s\n' "$(printf '%q' "$KORNI")"
    while IFS= read -r imya; do
      [ -z "$imya" ] && continue
      [ -d "$KORNI/$imya/.git" ] || printf '      git clone https://github.com/x10company/%s.git\n' "$imya"
    done <<< "$(gh repo list x10company --limit 100 --json name --jq '.[].name' 2>/dev/null | sort)"
  else
    printf '\n    Все репозитории организации уже здесь.\n'
  fi
else
  echo "    нет gh или нет входа — перечень пропущен"
  echo "    вручную: gh repo list x10company --limit 100"
fi

cat <<'KONEC'

Готово. Машина настроена, своды свежие.

Дальше:
  • чат открывать ИЗ каталога направления, а не из домашнего: иначе CLAUDE.md
    направления не читается и предметные правила не приезжают;
  • в уже открытом чате свежие своды подхватываются командой /reload-plugins.
    Новому чату она не нужна — он берёт с диска то, что там лежит.
KONEC
