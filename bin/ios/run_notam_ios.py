#!/usr/bin/env python3
"""
NOTAM Briefing -- iOS (a-Shell) foreground runner
EXPERIMENTAL -- see README "Running NOTAMv1 standalone on iPhone/iPad with
a-Shell" section.

a-Shell's `subprocess.Popen` does not appear to behave like a real OS
subprocess -- background server processes started that way produce no
output and don't seem to actually run (a-Shell hosts a single embedded
Python interpreter rather than spawning real OS processes for "python3").

This script avoids subprocess and os.exec entirely: it runs the server's
code directly inside this same a-Shell session via runpy. The server
keeps running for as long as this a-Shell session/tab stays open.

Usage (from the NOTAMv1 folder, in a-Shell):
    python3 bin/ios/run_notam_ios.py

Then switch to Safari (without closing a-Shell) and go to:
    http://localhost:8766

Press Ctrl+C in a-Shell (or force-quit the app) to stop the server.
"""
import os
import runpy
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # bin/ios -> bin -> ROOT


def main():
    os.chdir(ROOT)

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

    print()
    print("Starting the server in THIS a-Shell session (foreground).")
    print("Once you see READY below, switch to Safari and go to:")
    print("    http://localhost:8766")
    print("Keep a-Shell open in the background (or Split View) -- closing")
    print("it or letting iOS suspend it will stop the server.")
    print()
    print("Press Ctrl+C here to stop the server.")
    print("-" * 60)
    sys.stdout.flush()

    # Run the server's code directly in this process via runpy -- no new
    # process, no subprocess, no exec. This is the most likely method to
    # actually work given a-Shell's single-interpreter architecture.
    sys.path.insert(0, os.path.join(ROOT, 'src'))
    sys.argv = ['notam_server.py', '--no-browser']
    server_path = os.path.join(ROOT, 'src', 'notam_server.py')
    try:
        runpy.run_path(server_path, run_name='__main__')
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == '__main__':
    main()
