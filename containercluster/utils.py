import logging
import socket
import subprocess
import sys
import threading
import time

from paramiko.client import MissingHostKeyPolicy, SSHClient


__all__ = [
    "MultipleError",
    "SshSession",
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


class IgnoreMissingKeyPolicy(MissingHostKeyPolicy):

    def missing_host_key(self, *args):
        pass


class SshSession(object):

    log = logging.getLogger(__name__)

    def __init__(self, uid, addr, port, private_key_path):
        self.uid = uid
        self.addr = addr
        self.port = port
        self.private_key_path = private_key_path
        self.ssh_client = c = SSHClient()
        c.set_missing_host_key_policy(IgnoreMissingKeyPolicy())

    def __enter__(self):
        self.log.debug("Creating SSH connection to %s@%s (port %d) ...",
                       self.uid, self.addr, self.port)
        self.ssh_client.connect(hostname=self.addr,
                                port=self.port,
                                username=self.uid,
                                key_filename=self.private_key_path,
                                allow_agent=False,
                                look_for_keys=False)
        self.log.debug("... connected to %s@%s", self.uid, self.addr)
        return self.ssh_client

    def __exit__(self, *exc_info):
        try:
            self.log.debug("Closing connection to %s@%s ...",
                           self.uid, self.addr)
            self.ssh_client.close()
        except:
            pass
