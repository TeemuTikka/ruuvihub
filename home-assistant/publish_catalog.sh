#!/usr/bin/env bash
# Publish a Supervisor catalog branch containing only the app metadata and docs.
set -euo pipefail

version="${1:?An app version is required}"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
branch=refs/heads/home-assistant
index_dir="$(mktemp -d)"
trap 'rmdir "$index_dir"' EXIT

export GIT_AUTHOR_NAME='github-actions[bot]'
export GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

build_catalog_tree() (
    export GIT_INDEX_FILE="$index_dir/index"
    trap 'rm -f "$GIT_INDEX_FILE"' EXIT
    git read-tree --empty
    for file in repository.yaml ruuvihub/README.md ruuvihub/DOCS.md; do
        blob="$(git hash-object -w "home-assistant/$file")"
        git update-index --add --cacheinfo "100644,$blob,$file"
    done
    blob="$(sed "s/^version: \"0\.0\.0\"$/version: \"$version\"/" home-assistant/ruuvihub/config.yaml |
        git hash-object -w --stdin)"
    git update-index --add --cacheinfo "100644,$blob,ruuvihub/config.yaml"
    git write-tree
)

tree="$(build_catalog_tree)"
for attempt in 1 2 3; do
    parent=()
    if [[ -n "$(git ls-remote --heads origin "$branch")" ]]; then
        git fetch origin "$branch"
        current="$(git show FETCH_HEAD:ruuvihub/config.yaml | sed -n 's/^version: "\(.*\)"$/\1/p')"
        if [[ "$(printf '%s\n' "$current" "$version" | sort -V | tail -n 1)" == "$current" ]]; then
            echo "The store already advertises RuuviHub $current."
            exit 0
        fi
        parent=(-p FETCH_HEAD)
    fi
    commit="$(git commit-tree "$tree" "${parent[@]}" -m "Publish RuuviHub $version")"
    if git push origin "$commit:$branch"; then
        exit 0
    fi
    echo "Could not publish the catalog on attempt $attempt."
done
exit 1
