"""Exit-with-parent watchdog for app-supervised dashboard processes.

The macOS app (Story 18.1-002) runs ``bench dashboard`` as a supervised child.
On a normal quit the app terminates the process group, but a force-quit kills
only the app — so the dashboard watches its parent pid and terminates itself as
soon as it is reparented (orphaned). Termination goes through SIGTERM-to-self,
which reuses :mod:`local_code_bench.dashboard_lifecycle`'s graceful-shutdown
path and therefore also cleans the PID/state file.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable


def watch_parent(
    on_orphaned: Callable[[], None],
    *,
    getppid: Callable[[], int] = os.getppid,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until the parent process disappears, then call ``on_orphaned``.

    Orphaning is detected as any change of the parent pid (on POSIX the orphan
    is reparented, typically to pid 1 / launchd). A parent pid that is already
    1 at the first read counts as orphaned immediately.
    """

    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    original = getppid()
    while original != 1 and getppid() == original:
        sleep(poll_interval)
    on_orphaned()


def start_parent_watch(
    on_orphaned: Callable[[], None] | None = None,
    *,
    getppid: Callable[[], int] = os.getppid,
    poll_interval: float = 1.0,
) -> threading.Thread:
    """Run :func:`watch_parent` in a daemon thread and return it.

    The default action sends SIGTERM to the current process so the dashboard
    exits through its normal lifecycle handler.
    """

    action = on_orphaned if on_orphaned is not None else _terminate_self
    thread = threading.Thread(
        target=watch_parent,
        args=(action,),
        kwargs={"getppid": getppid, "poll_interval": poll_interval},
        name="parent-watch",
        daemon=True,
    )
    thread.start()
    return thread


def _terminate_self() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
