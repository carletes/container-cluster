import logging
import subprocess
import sys
import threading


__all__ = [
    "MultipleError",
    "parallel",
    "run",
]


LOG = logging.getLogger(__name__)


def run(cmdline, cwd=None, shell=True):
    LOG.debug("Running %s", cmdline)
    p = subprocess.Popen(cmdline, shell=shell, cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    if p.returncode:
        raise Exception(stderr.strip())
    return stdout.strip()


class MultipleError(Exception):

    def __init__(self, *args):
        super(MultipleError, self).__init__(*args)

    @property
    def message(self):
        return "\n".join(err.message for err in self.args)


def parallel(*tasks):
    results = []
    errors = []

    def _run(*args):
        func = args[0]
        args = args[1:]
        try:
            results.append(func(*args))
        except:
            LOG.debug("Caught error in %s%s", func, args, exc_info=True)
            _, exc_value, _ = sys.exc_info()
            errors.append(exc_value)

    threads = []
    for task in tasks:
        args = [task[0]]
        args.extend(task[1:])
        args = tuple(args)
        threads.append(threading.Thread(target=_run, args=args))

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    if errors:
        raise MultipleError(*errors)

    return results
