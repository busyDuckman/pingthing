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
