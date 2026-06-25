#!/usr/bin/env python3
"""
NOTAM Briefing — iOS (a-Shell) start
EXPERIMENTAL — see README "Running NOTAMv1 standalone on iPhone/iPad with
a-Shell" section.

a-Shell does not provide bash or ps, so this launcher is plain Python and
avoids both -- only the standard library is used.

Usage (from the NOTAMv1 folder, in a-Shell):
    python3 bin/ios/start_notam_ios.py
"""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # bin/ios -> bin -> ROOT
LOG_FILE = os.path.join(ROOT, 'logs', 'notam_server.log')
PID_FILE = os.path.join(ROOT, 'notam_server.pid')
URL = 'http://localhost:8766'


def _existing_server_pid():
    """Return a PID if notam_server.pid exists and that process is alive."""
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = check existence only, doesn't actually kill
    except ProcessLookupError:
        return None  # stale PID file from a previous run
    except PermissionError:
        return pid   # process exists but we can't signal it -- still "alive"
    return pid


def main():
    os.chdir(ROOT)

    existing = _existing_server_pid()
    if existing:
        print(f"A server appears to already be running (PID {existing}).")
        print(f"Try opening {URL} in Safari directly.")
        print(f"If that doesn't work, stop it first: python3 {os.path.join('bin', 'ios', 'stop_notam_ios.py')}")
        return

    # 1. First-run config setup
    if not os.path.exists(os.path.join(ROOT, 'config.py')):
        print("config.py not found -- running first-run setup.")
        rc = subprocess.call([sys.executable, os.path.join('src', 'setup_config.py')])
        if rc != 0:
            print("Setup failed or was cancelled.")
            return

    # 2. Regenerate maprender.js from current HTML
    subprocess.call([sys.executable, os.path.join('src', 'update_maprender.py')])

    # 3. Ensure logs directory exists
    os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)

    # 4. Start the server.
    # a-Shell's process/job-control model is limited and not guaranteed to
    # match bash. We start the server as a subprocess -- if a-Shell doesn't
    # support true background processes, the server will run only while
    # this script's process stays alive (i.e. while a-Shell itself is
    # foregrounded). See README for details and workarounds.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    with open(LOG_FILE, 'w') as log_f:
        proc = subprocess.Popen(
            [sys.executable, os.path.join('src', 'notam_server.py'), '--no-browser'],
            stdout=log_f, stderr=subprocess.STDOUT, env=env,
            start_new_session=True,  # detach from this process's session where possible
        )
    # log_f is now closed in the parent -- the child has its own fd via dup(),
    # so this avoids "Bad file descriptor" errors when this script exits
    # while the child is still writing to it.

    print(f"Server starting (PID {proc.pid}). Waiting for READY signal...")

    ready = False
    for _ in range(20):
        time.sleep(1)
        try:
            with open(LOG_FILE) as f:
                if any(line.startswith('READY') for line in f):
                    ready = True
                    break
        except FileNotFoundError:
            pass
        # Bail out early if the child already died
        if proc.poll() is not None:
            print(f"Server process exited early (code {proc.returncode}) -- check {LOG_FILE}")
            break

    if ready:
        print("Server ready.")
    else:
        print(f"Server did not confirm startup within 20s -- check {LOG_FILE}")
        print(f"It may still be starting; try opening {URL} in Safari anyway.")

    print()
    print(f"Open Safari and go to: {URL}")
    print("(Tip: Add to Home Screen for quick access -- see README)")
    print()
    print("Leave this a-Shell tab/session open to keep the server running.")
    print(f"To stop it: python3 {os.path.join('bin', 'ios', 'stop_notam_ios.py')}")


if __name__ == '__main__':
    main()
