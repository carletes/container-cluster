import os

from containercluster.config import Config


def test_config_dir_exists(config):
    assert os.path.isdir(config.config_dir)


def test_cfssl_progs_exist(config):
    assert os.access(config.cfssl_path, os.X_OK)
    assert os.access(config.cfssljson_path, os.X_OK)


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
