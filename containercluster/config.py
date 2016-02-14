import logging
import os
import platform
import pwd
import threading

import ipaddress
import requests
import yaml

from containercluster import ca, utils


__all__ = [
    "Config",
    "SSHKeyPair"
]


class Config(object):

    dir_lock = threading.RLock()

    log = logging.getLogger(__name__)

    def __init__(self, home=None):
        if home is None:
            self.home = os.path.expanduser("~")
        else:
            self.home = home
        self._clusters = {}

    def add_cluster(self, name, channel, n_etcd, size_etcd, n_workers,
                    size_worker, provider, location, network, subnet_length,
                    subnet_min, subnet_max, services_ip_range, dns_service_ip,
                    kubernetes_service_ip):
        cluster = {
            "provider": provider,
            "channel": channel,
            "location": location,
            "discovery_token": make_discovery_token(n_etcd),
            "network": network,
            "subnet_length": subnet_length,
            "subnet_min": subnet_min,
            "subnet_max": subnet_max,
            "services_ip_range": services_ip_range,
            "dns_service_ip": dns_service_ip,
            "kubernetes_service_ip": kubernetes_service_ip,
            "nodes": [],
        }
        for i in range(n_etcd):
            cluster["nodes"].append({
                "name": "%s-etcd%d" % (name, i),
                "type": "etcd",
                "size": size_etcd,
            })
        cluster["nodes"].append({
            "name": "%s-master" % (name,),
            "type": "master",
            "size": size_worker,
        })
        for i in range(n_workers):
            cluster["nodes"].append({
                "name": "%s-worker%d" % (name, i),
                "type": "worker",
                "size": size_worker,
            })
        self._clusters[name] = cluster

    def remove_cluster(self, name):
        try:
            del self._clusters[name]
        except KeyError:
            pass

    def save(self):
        fname = self.clusters_yaml_path
        self.log.info("Saving clusters definitions to %s", fname)
        clusters = dict(self._clusters)
        for c in clusters.values():
            c = dict(c)
            for k in ("network", "subnet_min", "subnet_max"):
                c[k] = str(c[k])
        with open(fname, "wt") as f:
            yaml.dump(clusters, f)

    def __repr__(self):
        return "<Config %r>" % (self._clusters,)

    @property
    def clusters(self):
        if not self._clusters:
            fname = self.clusters_yaml_path
            if os.access(fname, os.F_OK):
                self.log.info("Loading clusters definitions from %s", fname)
                with open(fname, "rt") as f:
                    self._clusters = yaml.load(f)
                for c in self._clusters.values():
                    for k in ("network", "subnet_min", "subnet_max"):
                        c[k] = ipaddress.ip_network(c[k])
            else:
                self._clusters = {}
        return dict(self._clusters)

    @property
    def clusters_yaml_path(self):
        return os.path.join(self.config_dir, "clusters.yaml")

    @property
    def config_dir(self):
        return self._ensure_dir(os.path.join(self.home, ".container-cluster"))

    @property
    def bin_dir(self):
        return self._ensure_dir(os.path.join(self.config_dir, "bin"))

    @property
    def ca_dir(self):
        return self._ensure_dir(os.path.join(self.config_dir, "ca"))

    @property
    def ssh_dir(self):
        return self._ensure_dir(os.path.join(self.config_dir, ".ssh"))

    @property
    def ca_cert_path(self):
        return ca.CA(self.ca_dir).cert_path

    @property
    def admin_cert_path(self):
        fname, _ = self._ensure_admin_tls()
        return fname

    @property
    def admin_key_path(self):
        _, fname = self._ensure_admin_tls()
        return fname

    def node_tls_paths(self, node_name, alt_names=None):
        return ca.CA(self.ca_dir).generate_cert(node_name, alt_names)

    def _ensure_admin_tls(self):
        return self.node_tls_paths(u"admin")

    @property
    def ssh_key_pair(self):
        return SSHKeyPair(self.ssh_dir)

    def _ensure_dir(self, dname):
        with self.dir_lock:
            if not os.access(dname, os.F_OK):
                self.log.info("Creating directory %s", dname)
                os.makedirs(dname)
            return dname

    def kubeconfig_path(self, cluster_name, master_ip):
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": cluster_name,
                    "cluster": {
                        "certificate-authority": self.ca_cert_path,
                        "server": "https://%s" % (master_ip,)
                    }
                },
            ],
            "users": [
                {
                    "name": "admin",
                    "user": {
                        "client-certificate": self.admin_cert_path,
                        "client-key": self.admin_key_path,
                    },
                },
            ],
            "contexts": [
                {
                    "name": cluster_name,
                    "context": {
                        "cluster": cluster_name,
                        "user": "admin",
                    }
                },
            ],
            "current-context": cluster_name,
        }
        fname = os.path.join(self.config_dir, "kubeconfig-%s" % (cluster_name,))
        with open(fname, "wt") as f:
            yaml.dump(kubeconfig, f)
        return fname


class SSHKeyPair(object):

    ssh_keygen_lock = threading.Lock()

    log = logging.getLogger(__name__)

    def __init__(self, dname):
        self.dname = dname

    @property
    def name(self):
        return ("container-cluster-%s-%s" %
                (pwd.getpwuid(os.geteuid()).pw_name,
                 platform.node().split(".")[0]))

    @property
    def _key_file_name(self):
        return os.path.join(self.dname, "id_rsa-%s" % (self.name,))

    @property
    def public_key(self):
        fname = self._ensure_ssh_key() + ".pub"
        with open(fname, "rt") as f:
            return f.read()

    @property
    def private_key_path(self):
        return self._ensure_ssh_key()

    def _ensure_ssh_key(self):
        fname = self._key_file_name
        with self.ssh_keygen_lock:
            if not os.access(fname, os.R_OK):
                self.log.info("Generating SSH key pair %s", fname)
                utils.run("ssh-keygen -f %s -N ''" % (fname,))
        return fname


LOG = logging.getLogger(__name__)


def make_discovery_token(size):
    res = requests.get("https://discovery.etcd.io/new?size=%d" % (size,))
    res.raise_for_status()
    token = res.content[len("https://discovery.etcd.io/"):]
    LOG.debug("New token for %d node(s): %s", size, token)
    return token
