# Раскладывает в текущий репозиторий четыре файла: проход CI, своды правил,
# исключения gitleaks и .gitignore.
#
# Новый репозиторий обвешивают не этим скриптом, а скачиванием четырёх файлов —
# см. свод hod-raboty, шаг 2. Труба `irm | iex` убрана 22.09.2026, тем же
# доводом, что и `curl | bash` у близнеца 21.09: она исполняет то, что лежит по
# адресу в эту секунду, а набор статический. Правка близнеца тогда прошла мимо
# этого файла — нашло направление, задачей ci-guard#5.
#
# Этот скрипт остаётся для ОБНОВЛЕНИЯ набора в уже живом репозитории. Запускают
# его с машины, где он лежит локально:
#
#   powershell -File \путь\к\ci-guard\primer\postavit.ps1
#
# Существующие файлы не трогает — сообщает и пропускает. Чтобы перезаписать,
# запусти с -Perezapisat; `.gitignore` и `.gitleaks.toml` не заменяются даже с
# ним.

param([switch]$Perezapisat)

$ErrorActionPreference = 'Stop'
$baza = 'https://raw.githubusercontent.com/x10company/ci-guard/main/primer'

# berech = не перезаписывать даже с -Perezapisat. Такой файл у проекта свой, и
# подмена его нашим может открыть то, что он закрывал: 04.09.2026 перезапись
# .gitignore открыла пять фикстур с настоящими ключами подписки.
$nabor = @(
    @{ ottuda = 'guard.yml';     kuda = '.github/workflows/guard.yml'; zachem = 'проход CI: ключи, gitleaks, синтаксис'; berech = $false },
    @{ ottuda = 'settings.json'; kuda = '.claude/settings.json';       zachem = 'регистрирует маркетплейс со сводами';   berech = $false },
    @{ ottuda = 'gitleaks.toml'; kuda = '.gitleaks.toml';              zachem = 'исключения поиска ключей, свои у проекта'; berech = $true },
    @{ ottuda = 'gitignore';     kuda = '.gitignore';                  zachem = 'без него в коммит уедет __pycache__'; berech = $true }
)

if (-not (Test-Path '.git')) {
    Write-Host '  Это не корень репозитория: каталога .git рядом нет.' -ForegroundColor Yellow
    Write-Host '  Перейди в корень нового репозитория и повтори.' -ForegroundColor Yellow
    return
}

Write-Host ''
Write-Host "  Раскладываю набор в $(Get-Location)" -ForegroundColor Cyan
Write-Host ''

$polozheno = 0
$propushcheno = 0

foreach ($f in $nabor) {
    $kuda = $f.kuda
    $katalog = Split-Path $kuda -Parent
    if ($katalog -and -not (Test-Path $katalog)) {
        New-Item -ItemType Directory -Path $katalog -Force | Out-Null
    }

    if ((Test-Path $kuda) -and $f.berech) {
        Write-Host ("    - {0,-32} уже есть, НЕ трогаю (свой у проекта)" -f $kuda) -ForegroundColor DarkGray
        $propushcheno++
        continue
    }

    if ((Test-Path $kuda) -and -not $Perezapisat) {
        Write-Host ("    - {0,-32} уже есть, пропускаю" -f $kuda) -ForegroundColor DarkGray
        $propushcheno++
        continue
    }

    # Перезаписываем только с резервной копией: вернуть прежний файл должно быть
    # можно без git — набор кладут и в репозиторий с грязным деревом.
    if (Test-Path $kuda) {
        $rezerv = "$kuda.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $kuda $rezerv
        Write-Host ("      прежний сохранён: {0}" -f $rezerv) -ForegroundColor DarkGray
    }

    try {
        Invoke-WebRequest -Uri "$baza/$($f.ottuda)" -OutFile $kuda -UseBasicParsing
        Write-Host ("    + {0,-32} {1}" -f $kuda, $f.zachem) -ForegroundColor Green
        $polozheno++
    }
    catch {
        Write-Host ("    ! {0,-32} не скачался: {1}" -f $kuda, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ''
Write-Host "  Положено: $polozheno, пропущено: $propushcheno" -ForegroundColor Cyan

if ($propushcheno -gt 0) {
    Write-Host '  Пропущенные не перезаписаны намеренно: у проекта могут быть свои.' -ForegroundColor DarkGray
    Write-Host '  .gitignore и .gitleaks.toml не заменяются даже с -Perezapisat:' -ForegroundColor DarkGray
    Write-Host '  подмена своего файла нашим может открыть то, что он закрывал.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  Дальше: закоммить эти файлы. После пуша проход CI заработает сам.' -ForegroundColor DarkGray
Write-Host '  Своды ставятся один раз на машину: claude plugin install x10-obshchie@x10company' -ForegroundColor DarkGray
Write-Host ''
