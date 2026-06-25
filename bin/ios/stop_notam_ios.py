#!/usr/bin/env python3
"""
NOTAM Briefing -- iOS (a-Shell) stop
EXPERIMENTAL -- see README "Running NOTAMv1 standalone on iPhone/iPad with
a-Shell" section.

a-Shell does not provide `ps`, so this reads the PID file the server writes
on startup (notam_server.pid) instead of listing processes.

This sends SIGTERM first (gentle, lets the server clean up its own PID/
info files), then checks whether the process actually stopped. If it's
still alive after a short wait, it escalates to SIGKILL (unconditional --
cannot be ignored or caught) and waits longer before giving up, since
a-Shell's process teardown can be slower than a normal OS process kill.

Usage (from the NOTAMv1 folder, in a-Shell):
    python3 bin/ios/stop_notam_ios.py
"""
import os
import signal
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # bin/ios -> bin -> ROOT
PID_FILE = os.path.join(ROOT, 'notam_server.pid')


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0 = check existence only, doesn't actually kill
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but we can't signal it -- treat as alive
    return True


def main():
    if not os.path.exists(PID_FILE):
        print("No notam_server.pid file found -- server may not be running,")
        print("or was started in a way that didn't record its PID.")
        print("If Safari still shows a response from the server, close the")
        print("a-Shell app completely (swipe up in the app switcher) to stop it.")
        return

    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError) as e:
        print(f"Could not read PID file: {e}")
        return

    if not _is_alive(pid):
        print(f"No process with PID {pid} -- it may have already stopped.")
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return

    # 1. Gentle stop: SIGTERM, lets the server clean up its own files.
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent stop signal to PID {pid}...")
    except Exception as e:
        print(f"Could not signal PID {pid}: {e}")

    # 2. Give it a moment, then check.
    stopped = False
    for _ in range(10):
        time.sleep(0.3)
        if not _is_alive(pid):
            print(f"Stopped server (PID {pid}).")
            stopped = True
            break

    if not stopped:
        # 3. Still alive after ~3s -- escalate to SIGKILL (unconditional).
        print(f"PID {pid} did not stop -- sending SIGKILL (forceful)...")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            print(f"Stopped server (PID {pid}).")
            stopped = True
        except Exception as e:
            print(f"Could not force-stop PID {pid}: {e}")
            print("Try force-quitting a-Shell completely, or restarting the device.")
            return

        if not stopped:
            # a-Shell's process teardown can be slow -- give SIGKILL up to
            # ~5s before concluding it didn't work, rather than ~0.5s.
            for _ in range(10):
                time.sleep(0.5)
                if not _is_alive(pid):
                    print(f"Stopped server (PID {pid}) with SIGKILL.")
                    stopped = True
                    break

    if not stopped:
        print(f"PID {pid} is still alive even after SIGKILL and a longer wait.")
        print("This may mean a-Shell's process model doesn't fully honour kill")
        print("signals, or it's just slow to tear down. The PID file has been")
        print("left in place so you can run this script again to re-check --")
        print("it will NOT report 'no server running' incorrectly.")
        print("If repeated attempts don't work, force-quit a-Shell completely,")
        print("or restart the device as a last resort.")
        return  # leave PID_FILE in place -- the process may still be alive

    try:
        os.remove(PID_FILE)
    except OSError:
        pass


if __name__ == '__main__':
    main()
