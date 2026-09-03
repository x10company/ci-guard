# Раскладывает в текущий репозиторий четыре файла: проход CI, своды правил,
# исключения gitleaks и .gitignore.
#
# Запуск из корня нового репозитория, одной строкой:
#   irm https://raw.githubusercontent.com/x10company/ci-guard/main/primer/postavit.ps1 | iex
#
# Существующие файлы не трогает — сообщает и пропускает. Чтобы перезаписать,
# скачай скрипт и запусти с -Perezapisat.

param([switch]$Perezapisat)

$ErrorActionPreference = 'Stop'
$baza = 'https://raw.githubusercontent.com/x10company/ci-guard/main/primer'

$nabor = @(
    @{ ottuda = 'guard.yml';     kuda = '.github/workflows/guard.yml'; zachem = 'проход CI: ключи, gitleaks, синтаксис' },
    @{ ottuda = 'settings.json'; kuda = '.claude/settings.json';       zachem = 'своды правил подключаются при открытии' },
    @{ ottuda = 'gitleaks.toml'; kuda = '.gitleaks.toml';              zachem = 'исключения поиска ключей, свои у проекта' },
    @{ ottuda = 'gitignore';     kuda = '.gitignore';                  zachem = 'без него в коммит уедет __pycache__' }
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

    if ((Test-Path $kuda) -and -not $Perezapisat) {
        Write-Host ("    - {0,-32} уже есть, пропускаю" -f $kuda) -ForegroundColor DarkGray
        $propushcheno++
        continue
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
    Write-Host '  Нужно заменить — скачай скрипт и запусти с -Perezapisat.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  Дальше: закоммить эти файлы. После пуша проход CI заработает сам,' -ForegroundColor DarkGray
Write-Host '  а открывший репозиторий получит своды правил без установки.' -ForegroundColor DarkGray
Write-Host ''
