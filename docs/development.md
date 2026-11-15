# Development

## Try it

```bash
python3 -m smelite
```

Open http://127.0.0.1:8420/ . The shipped config is in demo mode, so the page runs with synthetic nodes and no Asterisk. The first visit asks you to create the admin account.

Requires Python 3.11 or newer and aiohttp (`apt install python3-aiohttp` on Debian).

## Tests

```bash
python3 -m unittest discover -s tests
```

## Building the package

```bash
sudo apt install debhelper python3-aiohttp
./deploy/build-deb.sh              # both suites
./deploy/build-deb.sh bookworm     # Debian 12 only → dist/sm-elite_<ver>-1.deb12_all.deb
./deploy/build-deb.sh trixie       # Debian 13 only → dist/sm-elite_<ver>-1.deb13_all.deb
```

The `.deb` files land in `dist/`. The build runs the test suite first. Bump the upstream version in both `debian/changelog` (`dch -v <version>`) and `smelite/__init__.py`; the script refuses to build when they differ. At build time it rewrites the package version to `<ver>-1.deb12` or `<ver>-1.deb13` so the hardenedpenguin apt repo can put each build in the matching suite (`bookworm` or `trixie`).

## Releasing

Push a tag that matches the changelog upstream version, for example `git tag v0.2.8 && git push origin v0.2.8`. The Release workflow tests and builds on Debian 12 and 13, attaches both suite `.deb` files to a GitHub release, and asks the hardenedpenguin apt repository to publish them. That last step needs the `APT_REPO_DISPATCH_TOKEN` repository secret; without it the release still succeeds and the apt repository picks the packages up on its daily run.

## Layout

- `smelite/` the Python service: AMI, snapshots, stats cache, auth, history, admin, sounds, system info, DVSwitch, schedule, action log, settings backup, themes
- `web/` the dashboard (plain HTML, CSS, and JavaScript, with no build step)
- `config/` demo config and examples
- `debian/` Debian packaging
- `deploy/` package build script, config generator, Apache snippet, root helper, node status updater
- `docs/` the documentation pages linked from the README
- `data/` SQLite users, sessions, last heard, the action log, scheduled actions, and the node directory cache for the demo
- `images/` screenshots for the README

Node snapshots, last heard, and the favorites scan arrive on `/ws`. Commands are `POST /api/command` and stay off the socket.
