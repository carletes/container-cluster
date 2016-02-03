import logging
import socket
import subprocess
import sys
import threading
import time


__all__ = [
    "MultipleError",
    "parallel",
    "run",
    "wait_for_port_open",
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

    def __str__(self):
        return "\n".join(str(err) for err in self.args)

    def __iter__(self):
        return iter(self.args)


def parallel(tasks):
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


def wait_for_port_open(host, port, timeout=None, check_interval=0.1):
    start = time.time()
    while True:
        try:
            LOG.debug("Trying %s:%d ...", host, port)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(check_interval)
            sock.connect((host, port))
            sock.close()
        except:
            if timeout is not None:
                elapsed = time.time() - start
                if elapsed > timeout:
                    raise Exception("Timeout for %s:%d" % (host, port))
        else:
            elapsed = time.time() - start
            LOG.debug("Port %s:%d open after %g s", host, port, elapsed)
            return
        finally:
            sock.close()
