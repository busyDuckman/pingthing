# Dev tasks, needs uv (https://docs.astral.sh/uv/)

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# list recipes
default:
    @just --list

[private, unix]
require-uv:
    @command -v uv >/dev/null || { echo "This project requires uv, please install it first: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

[private, windows]
require-uv:
    @if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Host "This project requires uv, please install it first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1 }

# run from source, eg: just run --range 10.0.0.0/24
run *args: require-uv
    uv run pingthing {{args}}

# lint and test
check: lint test

test *args: require-uv
    uv run pytest {{args}}

lint: require-uv
    uv run ruff check src tests scripts

# refresh the bundled manufacturer list from the IEEE
update-mac-list: require-uv
    uv run python scripts/update_oui.py

# build the wheel and sdist into dist/
build: require-uv
    uv build

# install the pingthing command from this checkout
install: require-uv
    uv tool install --force --reinstall .

# Open the projects page on GitHub, because it a pain to nav github sometimes.
[unix]
github:
    #!/usr/bin/env bash
    set -euo pipefail
    remote=$(git remote get-url origin 2>/dev/null) || { echo "no 'origin' remote here"; exit 1; }
    # git@host:owner/repo.git, ssh://git@host/owner/repo and https://host/owner/repo.git
    # all end up as https://host/owner/repo.
    url=$(sed -E -e 's#^ssh://git@#https://#' -e 's#^git@([^:]+):#https://\1/#' -e 's#\.git$##' <<<"$remote")
    echo "$url"
    for opener in xdg-open open; do
        command -v "$opener" >/dev/null || continue
        "$opener" "$url" >/dev/null 2>&1 &
        exit 0
    done
    echo "no xdg-open or open on this machine - the address is above"

# Open the projects page on GitHub, because it a pain to nav github sometimes.
[windows]
[script("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File")]
[extension(".ps1")]
github:
    $ErrorActionPreference = "Stop"
    $remote = git remote get-url origin 2>$null
    if (-not $remote) { Write-Host "no 'origin' remote here"; exit 1 }
    # git@host:owner/repo.git, ssh://git@host/owner/repo and https://host/owner/repo.git
    # all end up as https://host/owner/repo.
    $url = $remote -replace '^ssh://git@', 'https://' -replace '^git@([^:]+):', 'https://$1/' -replace '\.git$', ''
    Write-Host $url
    Start-Process $url


