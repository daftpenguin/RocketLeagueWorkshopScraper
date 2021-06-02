#!/bin/bash

untracked=`git ls-files --other --exclude-standard --directory | egrep -v '/$' | wc -l`
if (( untracked > 0)); then
    git add --all
    git commit -m "Latest maps.json for ${d}"
    git push
fi
