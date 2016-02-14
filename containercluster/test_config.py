import os

import ipaddress
import yaml

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
                       "digitalocean", "lon1",
                       ipaddress.ip_network(u"172.16.0.0/16"), 24,
                       ipaddress.ip_network(u"172.16.1.0/24"),
                       ipaddress.ip_network(u"172.16.254.0"),
                       ipaddress.ip_network(u"172.17.0.0/16"),
                       ipaddress.ip_address(u"172.17.0.10"),
                       ipaddress.ip_address(u"172.17.0.1"))
    assert "test-cluster" in config.clusters
    assert config.clusters["test-cluster"]["discovery_token"]


def test_save(config):
    assert "test-cluster" not in config.clusters

    config.add_cluster("test-cluster", "alpha", 3, "512mb", 4, "1gb",
                       "digitalocean", "lon1",
                       ipaddress.ip_network(u"172.16.0.0/16"), 24,
                       ipaddress.ip_network(u"172.16.1.0"),
                       ipaddress.ip_network(u"172.16.254.0"),
                       ipaddress.ip_network(u"172.17.0.0/16"),
                       ipaddress.ip_address(u"172.17.0.10"),
                       ipaddress.ip_address(u"172.17.0.1"))

    config.save()
    new_config = Config(config.home)
    assert config.clusters == new_config.clusters


def test_node_tls_paths(config):
    cert_path, key_path = config.node_tls_paths(u"some-node-name",
                                                [u"127.0.0.1", u"192.168.0.1"])
    assert os.stat(cert_path).st_size
    assert os.stat(key_path).st_size


def test_admin_tls_paths(config):
    for fname in (config.admin_cert_path, config.admin_key_path):
        assert os.stat(fname).st_size


def test_kubeconfig_structure(config):
    with open(config.kubeconfig_path("test-cluster", "1.2.3.4")) as f:
        kubeconfig = yaml.load(f)

    current_context = kubeconfig["current-context"]

    for c in kubeconfig["contexts"]:
        if c["name"] == current_context:
            current_cluster = c["context"]["cluster"]
            current_user = c["context"]["user"]
            break
    else:
        raise AssertionError("Missing context '%s'" % (current_context,))

    for c in kubeconfig["clusters"]:
        if c["name"] == current_cluster:
            break
    else:
        raise AssertionError("Missing cluster '%s'" % (current_cluster,))

    for c in kubeconfig["users"]:
        if c["name"] == current_user:
            break
    else:
        raise AssertionError("Missing user '%s'" % (current_user,))
