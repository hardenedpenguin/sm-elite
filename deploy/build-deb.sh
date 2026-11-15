#!/usr/bin/env bash
# Build sm-elite .deb packages into dist/ for Debian 12 (bookworm) and/or
# Debian 13 (trixie). Needs: sudo apt install debhelper python3-aiohttp
#
# Usage:
#   ./deploy/build-deb.sh              # both suites
#   ./deploy/build-deb.sh bookworm     # sm-elite_<ver>-1.deb12_all.deb
#   ./deploy/build-deb.sh trixie       # sm-elite_<ver>-1.deb13_all.deb
#
# debian/changelog and smelite/__init__.py hold the upstream version (X.Y.Z).
# The built package version is X.Y.Z-1.deb12 or X.Y.Z-1.deb13 so the
# hardenedpenguin apt repo can put each build in the matching suite.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

suite_tag() {
  case "$1" in
    bookworm|deb12) echo deb12 ;;
    trixie|deb13) echo deb13 ;;
    *)
      echo "Unknown suite: $1 (use bookworm, trixie, or all)" >&2
      return 1
      ;;
  esac
}

build_one() {
  local suite="$1"
  local tag pkg_version bak
  tag="$(suite_tag "$suite")"

  local upstream code_version
  upstream="$(dpkg-parsechangelog -S Version)"
  if [[ "$upstream" == *deb1[23]* ]]; then
    echo "debian/changelog should hold the upstream version only, got $upstream" >&2
    exit 1
  fi
  code_version="$(python3 -c 'import smelite; print(smelite.__version__)')"
  if [ "$upstream" != "$code_version" ]; then
    echo "debian/changelog says $upstream but smelite/__init__.py says $code_version" >&2
    exit 1
  fi

  pkg_version="${upstream}-1.${tag}"
  bak="$(mktemp)"
  cp debian/changelog "$bak"
  restore_changelog() {
    mv -f "$bak" debian/changelog
  }
  trap restore_changelog EXIT

  # Keep the rest of the changelog entry; only the version on line 1 changes.
  sed -i "1s/([^)]*)/(${pkg_version})/" debian/changelog
  dpkg-buildpackage -us -uc -b
  mkdir -p dist
  mv ../sm-elite_"${pkg_version}"_all.deb dist/
  rm -f ../sm-elite_"${pkg_version}"_*.buildinfo ../sm-elite_"${pkg_version}"_*.changes

  restore_changelog
  trap - EXIT
  echo "Built dist/sm-elite_${pkg_version}_all.deb (${suite})"
}

targets=()
if [ $# -eq 0 ] || [ "${1:-}" = all ]; then
  targets=(bookworm trixie)
else
  targets=("$@")
fi

for suite in "${targets[@]}"; do
  build_one "$suite"
done

echo "Packages in dist/:"
ls -lh dist/sm-elite_*.deb
