"""Stop this process when its parent goes away.

A parent that holds the write end of our stdin closes it when it exits, even
when it is killed outright, so end-of-file on stdin means "stop". The desktop
app uses this twice: the sidecar stops when the Tauri shell goes away, and a
run stops when the sidecar goes away. Process groups and signals cannot cover
a parent that crashes, and on macOS there is no parent-death signal.
"""

import _thread
import os
import signal
import threading
import time

# Seconds left for a clean stop (final status written, events closed) before a hard exit
STOP_GRACE = 15.0


def _stop(grace: float, exit_code: int) -> None:
    handler = signal.getsignal(signal.SIGTERM)
    if os.name == "posix":
        # A real signal also wakes a main thread blocked on a lock; SIG_IGN means
        # a stop is already under way, and the default action ends the process
        os.kill(os.getpid(), signal.SIGTERM)
    elif callable(handler):
        _thread.interrupt_main(signal.SIGTERM)
    elif handler != signal.SIG_IGN:
        os._exit(exit_code)
    time.sleep(grace)
    os._exit(exit_code)


def stop_when_stdin_closes(grace: float = STOP_GRACE, exit_code: int = 130) -> None:
    """Send ourselves SIGTERM when stdin reaches end-of-file, and exit hard `grace` seconds later."""

    def watch():
        try:
            while os.read(0, 4096):
                pass  # nothing is sent on this channel; only its closing counts
        except OSError:
            pass
        _stop(grace, exit_code)

    threading.Thread(target=watch, name="lifeline", daemon=True).start()
