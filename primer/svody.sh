#!/usr/bin/env bash
# Свежи ли своды правил. Ничего не меняет, только смотрит.
#
# Зачем отдельно от mashina.sh: тот настраивает и обновляет, а этот отвечает на
# вопрос «надо ли». Обновляться посреди задачи нельзя — критерий приёмки уедет
# под ногами, — поэтому сперва смотрят, а решают потом.
#
#   bash svody.sh
#
# Код возврата: 0 — диск свежий, 1 — отстал, 2 — посмотреть не удалось.
#
# Версий три, и путать их дорого:
#   опубликовано  что лежит на GitHub;
#   на диске      что скачано этой машиной — двигает claude plugin update;
#   в сессии      с чем работает конкретный чат — двигает /reload-plugins.
# Третью этот скрипт узнать не может: он не внутри чата. Её печатает сам чат,
# когда вызывает свод, — в пути загрузки стоит номер.
set -u

BAZA="$HOME/.claude/plugins"
MARKET="$BAZA/marketplaces/x10company"
USTANOVLENO="$BAZA/installed_plugins.json"

[ -d "$MARKET/.git" ] || { echo "  маркетплейс не подключён: запусти mashina.sh"; exit 2; }
[ -f "$USTANOVLENO" ] || { echo "  плагины не установлены: запусти mashina.sh"; exit 2; }

git -C "$MARKET" fetch -q origin 2>/dev/null || {
  echo "  до GitHub не достучаться — сужу по тому, что скачано ранее"; }

otstal=0
printf '  %-16s %-12s %-12s %s\n' плагин опубликовано "на диске" ""
for p in x10-obshchie x10-hozyaystvo; do
  tam=$(git -C "$MARKET" show "origin/main:plugins/$p/.claude-plugin/plugin.json" 2>/dev/null \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  tut=$(sed -n "/\"$p@x10company\"/,/]/p" "$USTANOVLENO" \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$tam" ] || tam="?"
  [ -n "$tut" ] || tut="нет"
  if [ "$tam" = "$tut" ]; then metka="свежо"; else metka="ОТСТАЛ"; otstal=1; fi
  printf '  %-16s %-12s %-12s %s\n' "$p" "$tam" "$tut" "$metka"
done

echo
if [ "$otstal" -eq 1 ]; then
  cat <<'KONEC'
  Диск этой машины отстал. Обновлять — МЕЖДУ задачами, не внутри:
  задача принимается против той версии свода, с которой начата.

    claude plugin update x10-obshchie@x10company
    claude plugin update x10-hozyaystvo@x10company

  После этого в каждом уже открытом чате — /reload-plugins.
  Новому чату она не нужна: он берёт с диска то, что там лежит.
KONEC
  exit 1
fi

cat <<'KONEC'
  Диск свежий. Но это ещё не значит, что свеж ЧАТ.

  Чат держит текст, загруженный при запуске или при последней /reload-plugins.
  Узнать его версию можно единственным способом — попросить сессию вызвать свод
  и назвать каталог загрузки: номер стоит прямо в пути.

      .../plugins/cache/x10company/x10-obshchie/2.22.0/skills/hod-raboty
                                               ^^^^^^ вот она

  Номер в списке плагинов про это не говорит: он показывает скачанное.
KONEC
exit 0
