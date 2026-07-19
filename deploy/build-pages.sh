#!/usr/bin/env sh
set -eu

rm -rf dist
mkdir -p dist
cp -R static dist/static
cp -R stylesheets dist/stylesheets
cp deploy/pages-index.html dist/index.html
cp deploy/pages-headers dist/_headers
cp deploy/pages-redirects dist/_redirects
