#!/bin/bash
set -euo pipefail

source .env

deployed=0
while IFS='=' read -r key value; do
    [[ "$key" == OBSIDIAN_PLUGINS_PATH* ]] || continue
    path="${!key}"
    mkdir -p "$path"
    cp main.js manifest.json "$path/"
    echo "Deployed to $path"
    deployed=$((deployed + 1))
done < .env

if [[ $deployed -eq 0 ]]; then
    echo "Error: No OBSIDIAN_PLUGINS_PATH variables found in .env" >&2
    exit 1
fi
