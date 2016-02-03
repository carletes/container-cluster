import codecs
import os
import platform
import pwd
import tempfile

import mockssh

import pytest

from containercluster import core


HOSTNAME = platform.node()
UID = pwd.getpwuid(os.geteuid()).pw_name


def test_cloud_config_template(mock_cluster):
    for node in mock_cluster.nodes:
        assert node.cloud_config_template.startswith("#cloud-config")


def test_create_cluster(mock_cluster):
    assert len(mock_cluster.nodes) == 7
    for node in mock_cluster.nodes:
        assert node.name.startswith(mock_cluster.name)


def test_destroy_cluster(mock_cluster):
    tls_paths = []
    for n in mock_cluster.nodes:
        for fname in mock_cluster.config.node_tls_paths(n.name):
            assert os.access(fname, os.F_OK)
            tls_paths.append(fname)
    assert tls_paths

    mock_cluster.destroy_nodes()
    for fname in tls_paths:
        assert not os.access(fname, os.F_OK)


def test_node_tls_data(mock_cluster):
    for node in mock_cluster.nodes:
        assert node.tls_cert.startswith("-----BEGIN CERTIFICATE-----")
        assert node.tls_key.startswith("-----BEGIN RSA PRIVATE KEY-----")


def test_env_variables(mock_cluster):
    env = dict(mock_cluster.env_variables)
    for k in ("ETCDCTL_CA_FILE", "ETCDCTL_CERT_FILE"):
        with open(env[k], "rt") as f:
            assert f.read().startswith("-----BEGIN CERTIFICATE-----")
    with open(env["ETCDCTL_KEY_FILE"], "rt") as f:
        assert f.read().startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert env["ETCDCTL_ENDPOINT"].split(",")


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
        with core.SshSession(uid, s.host, s.port, private_key_path) as session:
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
