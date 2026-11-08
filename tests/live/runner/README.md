# The live rig's Actions runner

The live rig only runs on novatest-hl, where the game installs live under their
own unix users. So `.gitea/workflows/live-rig.yml` runs on a runner on that
host, not on the shared docker runner (gitea-runner-hl):

- **act_runner in host mode**, as the system user `gitea-runner`
  (home `/var/lib/gitea-runner`), from the `act-runner` systemd unit.
- **Registered to lime/nova-hub alone**, with the one label `novatest`. No other
  repo's workflows can land on it, and no job of this repo lands on it unless it
  asks for `runs-on: novatest`.
- **Capacity 1.** run.sh also takes `/run/lock/novatest-rig.lock`, so a nightly
  run and a run someone started over ssh wait for each other, not collide.
- **One privilege:** `/etc/sudoers.d/gitea-runner` lets it run
  `tests/live/nightly.sh` as `novahub-t`, and nothing else. nightly.sh moves the
  rig checkout forward (main only, fast-forward only, never over local changes)
  and runs `run.sh --setup`; a dispatch on another branch goes through `--try`.

## Installing it

From the rig checkout, as root:

    sudo sh /srv/novatest/nova-hub/tests/live/runner/install.sh

Then register once, with a token from the repo's
Settings → Actions → Runners → Create new runner
(`https://gitea-hl.taild204f.ts.net/lime/nova-hub/settings/actions/runners`):

    sudo -u gitea-runner sh -c 'cd /var/lib/gitea-runner && act_runner --config /etc/act_runner/config.yaml register --no-interactive \
      --instance https://gitea-hl.taild204f.ts.net --name novatest-hl --labels novatest:host --token <TOKEN>'
    sudo systemctl start act-runner.service

The token is used once and need not be kept; the registration is
`/var/lib/gitea-runner/.runner`. Deleting that file and the runner in gitea's
settings undoes it.

## Running it

- Nightly at 03:00 UTC, on main.
- By hand: Actions → live-rig → Run workflow, on main or a branch.
- Results land in gitea's Actions tab and as the commit's status. They also
  reach OpenObserve's `ci_runs` like every other workflow, which feeds
  `ci_main_failed` and the `ci_live_rig_not_running` alert (no completed run in 26 h).
- The dosemu transcripts of a failed run stay on the rig, in `/srv/novatest/logs`.
