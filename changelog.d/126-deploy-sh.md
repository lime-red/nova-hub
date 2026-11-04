### Changed
- **`deploy.sh` is the same on every host** (#126). It works from the service
  user's home (`~/nova-hub`, `~/nova-data`) instead of assuming
  `/home/novahub`, finds the database where `config.toml` says, and no longer
  needs the `sqlite3` command.
  - It now syncs the venv from `uv.lock` (`uv sync --frozen --no-dev`), so a
    deploy no longer needs a separate package step. Before anything moves, a
    dry run against the target's lock stops the deploy if the venv would lose a
    package, go back a version, or be rebuilt;
    `NOVA_HUB_ALLOW_VENV_LOSSES=1` lets an intended one through.
  - `deploy.sh --ref <branch|commit>` pins a hub to something that is not a tag,
    for the standing test hub.
  - `deploy.sh --here` is for dev: it builds the tree as it is (venv with the
    dev tools, frontend, migrations), backs up the database first, and restarts
    the hub's user unit.
  - A pinned deploy refuses to run from inside the tree it is about to check
    out. The playbook puts the copy at `~/deploy.sh`.

### Removed
- **The rig's viewer hub, `tests/live/serve.py`** (#126). The standing test hub
  on novatest-hl (`https://novatest-hl.<tailnet>.ts.net`) replaces it.
