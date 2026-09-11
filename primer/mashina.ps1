#Requires -Version 5.1
<#
Разовая настройка машины под работу с репозиториями x10company.

Зачем отдельно от postavit.sh: тот обвешивает ОДИН репозиторий, а этот
настраивает машину целиком — подпись коммитов, доступ к GitHub и своды правил.
Первое ставится на машину раз и навсегда, второе — в каждый новый репозиторий.

Запускать можно сколько угодно раз: всё, что уже сделано, пропускается.

    powershell -ExecutionPolicy Bypass -File mashina.ps1

Тем же скриптом обновляются своды: повторный запуск подтягивает свежие версии
плагинов. Отдельной команды для этого помнить не надо.
#>

$ErrorActionPreference = 'Stop'

function Shag($n, $tekst) { "" ; "[$n] $tekst" }
function Est($cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

# --- 1. Что должно быть до нас -----------------------------------------------
Shag 1 'Проверяю, что установлено'
foreach ($c in 'git', 'claude') {
    if (Est $c) { "    $c — есть: $((Get-Command $c).Source)" }
    else { "    $c — НЕТ. Поставить и запустить скрипт заново."; exit 1 }
}

# --- 2. Подпись коммитов ------------------------------------------------------
# Адрес именно этот. Личную почту в коммиты не кладём: она попадает в публичную
# историю. Адрес вида <id>+<логин>@users.noreply.github.com площадка привязывает
# к учётной записи, и коммит считается своим.
Shag 2 'Подпись коммитов'
$imya  = 'Ivan Chernyshuk'
$pochta = '306788272+ivanchernyshuk@users.noreply.github.com'
git config --global user.name  $imya
git config --global user.email $pochta
"    user.name  = $(git config --global user.name)"
"    user.email = $(git config --global user.email)"

# --- 3. Доступ к GitHub -------------------------------------------------------
# По HTTPS через менеджер учётных данных Windows. Ключ SSH не нужен и не
# заводится: на канонической машине его нет, и всё работает.
Shag 3 'Доступ к GitHub'
git config --global credential.helper manager
"    credential.helper = $(git config --global credential.helper)"
"    Логин спросят один раз, при первом обращении к приватному репозиторию."

# --- 4. Своды правил ----------------------------------------------------------
# Маркетплейс приватный, поэтому шаг 3 обязан идти раньше этого.
Shag 4 'Своды правил'
$market = 'x10company/claude-skills'
$spisok = (claude plugin marketplace list 2>&1 | Out-String)
if ($spisok -match 'x10company') {
    "    маркетплейс уже подключён, обновляю"
    claude plugin marketplace update x10company 2>&1 | ForEach-Object { "    $_" }
} else {
    "    подключаю маркетплейс $market"
    claude plugin marketplace add $market 2>&1 | ForEach-Object { "    $_" }
}

foreach ($p in 'x10-obshchie', 'x10-hozyaystvo') {
    $vyvod = (claude plugin update "$p@x10company" 2>&1 | Out-String)
    if ($vyvod -match 'not installed|не установлен|No plugin') {
        "    ставлю $p"
        claude plugin install "$p@x10company" 2>&1 | ForEach-Object { "    $_" }
    } else {
        $vyvod.Trim().Split("`n") | ForEach-Object { "    $($_.Trim())" }
    }
}

# --- 5. Проверка --------------------------------------------------------------
Shag 5 'Что получилось'
claude plugin list 2>&1 | ForEach-Object { "    $_" }
""
"    Должно быть по ОДНОЙ строке на плагин, обе scope: user."
"    Больше одной — плагин включён ещё и файлом проекта, а это замораживает версию."

# --- 6. Что дальше ------------------------------------------------------------
""
"Готово. Дальше:"
""
"  1. Забрать репозиторий, по HTTPS:"
"       git clone https://github.com/x10company/<имя>.git"
"     Первый раз откроется вход в GitHub — после него доступ запомнится."
""
"  2. Открывать чат ИЗ каталога репозитория, а не из корня диска:"
"     иначе CLAUDE.md направления не читается и правила не приезжают."
""
"  3. В уже открытом чате свежие своды подхватываются командой /reload-plugins."
"     Новому чату она не нужна: он берёт с диска то, что там лежит."
