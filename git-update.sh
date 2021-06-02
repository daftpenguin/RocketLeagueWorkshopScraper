#!/bin/bash

d=`date +%s`

untracked=`git ls-files -m | egrep -v '/$' | wc -l`
if (( untracked > 0)); then
    git add --all
    git commit -m "Latest maps.json for ${d}"
    git push
fi
