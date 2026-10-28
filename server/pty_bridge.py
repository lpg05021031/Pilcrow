#!/usr/bin/env python3
"""PTY bridge for Pilcrow's terminal panel.

Runs a program (default: the user's login shell) in a pseudo-terminal and relays
bytes over stdin/stdout, so Node can drive a real terminal without native addons.
Resize requests arrive on file descriptor 3 as JSON lines: {"cols": 120, "rows": 32}.
"""
import errno
import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios


def set_size(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(rows, 2), max(cols, 2), 0, 0))


def write_all(fd, data):
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EINTR):
                select.select([], [fd], [], 1.0)
                continue
            raise
        view = view[n:]


def main():
    cols = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    argv = sys.argv[3:] or [os.environ.get("SHELL") or "/bin/bash", "-l"]

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except Exception as e:  # noqa: BLE001
            os.write(2, f"pilcrow: cannot start {argv[0]}: {e}\r\n".encode())
            os._exit(127)

    set_size(master, cols, rows)
    ctrl_open = True
    try:
        os.fstat(3)
    except OSError:
        ctrl_open = False
    ctrl_buf = b""
    stdin_open = True

    while True:
        fds = [master]
        if stdin_open:
            fds.append(0)
        if ctrl_open:
            fds.append(3)
        try:
            ready, _, _ = select.select(fds, [], [], 1.0)
        except InterruptedError:
            continue
        if master in ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""
            if not data:
                break
            write_all(1, data)
        if 0 in ready:
            data = os.read(0, 65536)
            if not data:
                stdin_open = False
                os.kill(pid, signal.SIGHUP)
            else:
                write_all(master, data)
        if ctrl_open and 3 in ready:
            chunk = os.read(3, 4096)
            if not chunk:
                ctrl_open = False
            else:
                ctrl_buf += chunk
                while b"\n" in ctrl_buf:
                    line, ctrl_buf = ctrl_buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                        set_size(master, int(msg["cols"]), int(msg["rows"]))
                    except Exception:  # noqa: BLE001
                        pass

    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
    except ChildProcessError:
        code = 0
    sys.exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    main()
