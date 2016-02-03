import os

from containercluster.config import Config


def test_config_dir_exists(config):
    assert os.path.isdir(config.config_dir)


def test_ca_cert_file_exists(config):
    assert os.access(config.ca_cert_path, os.F_OK)


def test_ssh_key(config):
    key = config.ssh_key_pair
    assert key.public_key.startswith("ssh-rsa ")


def test_add_cluster(config):
    assert "test-cluster" not in config.clusters

    config.add_cluster("test-cluster", "alpha", 3, "512mb", 4, "1gb",
                       "digitalocean", "lon1")
    assert "test-cluster" in config.clusters
    assert config.clusters["test-cluster"]["discovery_token"]


def test_save(config):
    assert "test-cluster" not in config.clusters

    config.add_cluster("test-cluster", "alpha", 3, "512mb", 4, "1gb",
                       "digitalocean", "lon1")

    config.save()
    new_config = Config(config.home)
    assert "test-cluster" in new_config.clusters


def test_node_tls_paths(config):
    cert_path, key_path = config.node_tls_paths(u"some-node-name",
                                                [u"127.0.0.1", u"192.168.0.1"])
    assert os.stat(cert_path).st_size
    assert os.stat(key_path).st_size


def test_admin_tls_paths(config):
    for fname in (config.admin_cert_path, config.admin_key_path):
        assert os.stat(fname).st_size
