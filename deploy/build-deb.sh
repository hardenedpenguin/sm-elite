#!/usr/bin/env bash
# Build the sm-elite .deb into dist/. Needs: sudo apt install debhelper python3-aiohttp
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
version="$(dpkg-parsechangelog -S Version)"
code_version="$(python3 -c 'import smelite; print(smelite.__version__)')"
[ "$version" = "$code_version" ] || { echo "debian/changelog says $version but smelite/__init__.py says $code_version" >&2; exit 1; }
dpkg-buildpackage -us -uc -b
mkdir -p dist
mv ../sm-elite_"$version"_all.deb dist/
rm -f ../sm-elite_"$version"_*.buildinfo ../sm-elite_"$version"_*.changes
echo "Built dist/sm-elite_${version}_all.deb"
