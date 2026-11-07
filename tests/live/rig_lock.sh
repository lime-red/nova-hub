# One rig run at a time. Sourced by run.sh and nightly.sh; not run on its own.
#
# Every run restores the same installs and fixtures, so a second one (the
# nightly, say, while someone is trying a branch) would wreck both, and
# nightly.sh must not move the checkout under a run either.
#
#   rig_lock "$0" "$@"
#
# Unless this process is already under the lock (NOVATEST_RIG_LOCKED, which
# survives exec, so --try's tried tree and nightly.sh's run.sh go straight on),
# it runs the script again under flock(1) and exits with its status. It waits
# up to an hour for another run, then gives up, names that run, and exits 75.
#
# The lock is held by the flock process alone. -o keeps it from the script and
# everything the script starts, so a game or IB process left behind by a
# crashed run cannot keep the rig locked: the lock goes when the run's own
# process ends, however it ends. Only a run that is alive and hung can hold it,
# and that is what the message names.
RIG_LOCK=/run/lock/novatest-rig.lock

rig_lock() {
    [[ -z "${NOVATEST_RIG_LOCKED:-}" ]] || return 0
    export NOVATEST_RIG_LOCKED=1
    local rc=0
    flock -n -o -E 75 "$RIG_LOCK" "$@" || rc=$?
    if (( rc == 75 )); then
        echo "another rig run holds $RIG_LOCK; waiting for it (up to an hour)" >&2
        rc=0
        flock -w 3600 -o -E 75 "$RIG_LOCK" "$@" || rc=$?
        if (( rc == 75 )); then
            echo "gave up waiting for $RIG_LOCK; the run holding it:" >&2
            local fd pid
            for fd in /proc/[0-9]*/fd/*; do
                if [[ "$(readlink "$fd" 2>/dev/null)" == "$RIG_LOCK" ]]; then
                    pid=${fd#/proc/}; pid=${pid%%/*}
                    ps -o pid=,user=,lstart=,args= -p "$pid" 2>/dev/null || true
                    ps -o pid=,user=,lstart=,args= --ppid "$pid" 2>/dev/null || true
                fi
            done | sort -u >&2
        fi
    fi
    exit "$rc"
}
