#!/usr/bin/python3

# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=invalid-name

import sys
from almost_make.utils.printUtil import cprint


class ErrorUtil:
    stopOnError = True
    silent = False

    # On error, report [message] depending on [SILENT] and [STOP_ON_ERROR]
    def reportError(self, message):
        if not self.silent or self.stopOnError:
            cprint(f"{message}\n", "RED", file=sys.stderr)

        if self.stopOnError:
            print("Stopping.")
            sys.exit(1)

    def logWarning(self, message):
        if not self.silent:
            cprint(f"Warning: {message}\n", "YELLOW", file=sys.stderr)

    # Option-setting functions
    def setStopOnError(self, stopOnErr):
        self.stopOnError = stopOnErr

    def setSilent(self, silent):
        self.silent = silent
