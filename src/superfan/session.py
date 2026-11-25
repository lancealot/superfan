"""Taking fan control from the BMC, and handing it back."""

from __future__ import annotations

import fcntl
import logging
import os
import time
from typing import IO

from .bmc import EXIT_MODES, MODE_FULL, MODE_STANDARD, Bmc, BmcError, mode_name

log = logging.getLogger(__name__)


class Busy(Exception):
    """Another superfan process is already commanding the fans."""


def acquire_lock(state_dir: str) -> IO[str]:
    """Take the single-controller lock. Keep the returned handle open while in control."""
    os.makedirs(state_dir, exist_ok=True)
    handle = open(os.path.join(state_dir, "lock"), "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.seek(0)
        owner = handle.read().strip()
        handle.close()
        raise Busy("pid %s" % owner if owner else "another process") from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


class Session:
    """Remembers the BMC's own fan mode on takeover so release can put it back."""

    def __init__(self, bmc: Bmc, state_dir: str, exit_mode: str = "previous", retry_delay: float = 1.0) -> None:
        self.bmc = bmc
        self.exit_mode = exit_mode
        self.retry_delay = retry_delay
        self._previous = os.path.join(state_dir, "previous_mode")

    def takeover(self) -> int:
        """Put the BMC in Full mode. Returns the mode it was in."""
        mode = self.bmc.get_mode()
        if self.bmc.dry_run:
            return mode
        if mode == MODE_FULL:
            # Already ours to command: an earlier run died without releasing, or `superfan set` was used.
            # Keep what was saved then, and do not re-enter Full mode, which may reset every zone to 100 %.
            return mode
        try:
            os.makedirs(os.path.dirname(self._previous), exist_ok=True)
            with open(self._previous, "w") as handle:
                handle.write(str(mode))
        except OSError as exc:
            log.warning("cannot record the BMC's previous fan mode (%s); will restore Standard", exc)
        self.bmc.set_mode(MODE_FULL)
        return mode

    def _target(self) -> int:
        if self.exit_mode != "previous":
            return EXIT_MODES[self.exit_mode]
        try:
            with open(self._previous) as handle:
                mode = int(handle.read().strip())
        except (OSError, ValueError):
            return MODE_STANDARD
        return MODE_STANDARD if mode == MODE_FULL else mode

    def release(self) -> bool:
        """Return fan control to the BMC. Safe to call when it already has it."""
        if self.bmc.dry_run:
            return True
        target = self._target()
        error = None
        for attempt in range(3):
            if attempt:
                time.sleep(self.retry_delay)
            try:
                if self.bmc.get_mode() == MODE_FULL:
                    self.bmc.set_mode(target)
                    log.info("returned fan control to the BMC (%s mode)", mode_name(target))
                try:
                    os.unlink(self._previous)
                except OSError:
                    pass
                return True
            except BmcError as exc:
                error = exc
        log.critical("could not return fan control to the BMC: %s", error)
        return False
