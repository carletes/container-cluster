import codecs
import os
import pwd
import tempfile

import mockssh
import pytest

from containercluster import utils


def test_multiple_error_message():
    err = utils.MultipleError(KeyError("foo"), ValueError("bar"))
    assert str(err) == "'foo'\nbar"


def test_errors_in_parallel():
    def divide_by_zero(n):
        return n / 0

    def key_error(k):
        return {}[k]

    try:
        divide_by_zero(42)
    except ZeroDivisionError as err:
        divide_by_zero_err = str(err)

    with pytest.raises(utils.MultipleError) as err:
        utils.parallel(((utils.run, "echo foo"),
                        (key_error, "no-such-key"),
                        (divide_by_zero, 42),
                        (utils.run, "echo bar")))
    errors = set(str(exc) for exc in err.value)
    assert errors == set(("'no-such-key'", divide_by_zero_err))


def ssh_private_key_path():
    ssh_dir = os.path.expanduser("~/.ssh")
    for fname in ("id_rsa",):
        fname = os.path.join(ssh_dir, fname)
        if os.access(fname, os.F_OK):
            return fname


needs_ssh_private_key = pytest.mark.skipif(ssh_private_key_path() is None,
                                           reason="Missing SSH private key")


@pytest.yield_fixture(scope="function")
def ssh_session():
    uid = pwd.getpwuid(os.geteuid()).pw_name
    private_key_path = ssh_private_key_path()
    with mockssh.Server({uid: private_key_path}) as s:
        with utils.SshSession(uid, s.host, s.port, private_key_path) as session:
            yield session


@needs_ssh_private_key
def test_ssh_session(ssh_session):
    _, stdout, _ = ssh_session.exec_command("ls /")
    assert "etc" in (codecs.decode(bit, "utf8")
                     for bit in stdout.read().split())


@needs_ssh_private_key
def test_sftp_session(ssh_session):
    target_dir = tempfile.mkdtemp()
    target_fname = os.path.join(target_dir, "foo")
    assert not os.access(target_fname, os.F_OK)

    ssh_session.open_sftp().put(__file__, target_fname)
    assert os.access(target_fname, os.F_OK)
