import logging
import json
import os
import platform
import pwd
import threading

from contextlib import closing

import requests
import yaml

from containercluster import utils


__all__ = [
    "Config",
    "SSHKeyPair"
]


class Config(object):

    log = logging.getLogger(__name__)

    def __init__(self, home=None):
        if home is None:
            self.home = os.path.expanduser("~")
        else:
            self.home = home
        self._clusters = {}

    def add_cluster(self, name, channel, n_etcd, size_etcd, n_workers,
                    size_worker, provider, location):
        cluster = {
            "provider": provider,
            "channel": channel,
            "location": location,
            "discovery_token": make_discovery_token(n_etcd),
            "nodes": [],
        }
        for i in range(n_etcd):
            cluster["nodes"].append({
                "name": "%s-etcd%d" % (name, i),
                "type": "etcd",
                "size": size_etcd,
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
        with open(fname, "wt") as f:
            yaml.dump(self._clusters, f)

    @property
    def clusters(self):
        if not self._clusters:
            fname = self.clusters_yaml_path
            if os.access(fname, os.F_OK):
                self.log.info("Loading clusters definitions from %s", fname)
                with open(fname, "rt") as f:
                    self._clusters = yaml.load(f)
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
        fname = os.path.join(self.ca_dir, "ca-cert.pem")
        if not os.access(fname, os.F_OK):
            self.log.info("Creating CoreOS CA cert and key")
            try:
                utils.run("%(cfssl)s gencert -initca %(csr)s | "
                          "%(cfssljson)s -bare ca-cert -" % {
                              "cfssl": self.cfssl_path,
                              "csr": self.ca_csr_path,
                              "cfssljson": self.cfssljson_path,
                          },
                          shell=True, cwd=self.ca_dir)
            except:
                try:
                    os.unlink(fname)
                except:
                    pass
                raise
        return fname

    @property
    def ca_csr_path(self):
        fname = os.path.join(self.ca_dir, "ca-csr.pem")
        if not os.access(fname, os.F_OK):
            self.log.info("Creating CoreOS CA CSR file %s", fname)
            with open(fname, "wt") as f:
                json.dump({
                    "CN": "CoreOS cluster demo CA",
                    "hosts": [
                        "localhost",
                    ],
                    "key": {
                        "algo": "rsa",
                        "size": 2048
                    },
                    "names": [
                        {
                            "O": "CoreOS cluster demo",
                        }
                    ]
                }, f, indent=2)
        return fname

    @property
    def ca_config_path(self):
        fname = os.path.join(self.ca_dir, "ca-config.json")
        if not os.access(fname, os.F_OK):
            self.log.info("Creating CoreOS CA config file %s", fname)
            with open(fname, "wt") as f:
                json.dump({
                    "signing": {
                        "default": {
                            "expiry": "43800h"
                        },
                        "profiles": {
                            "client-server": {
                                "expiry": "43800h",
                                "usages": [
                                    "signing",
                                    "key encipherment",
                                    "server auth",
                                    "client auth"
                                ]
                            }
                        }
                    }
                }, f, indent=2)
        return fname

    @property
    def cfssl_path(self):
        return self._ensure_cfssl_bin("cfssl")

    @property
    def cfssljson_path(self):
        return self._ensure_cfssl_bin("cfssljson")

    @property
    def ssh_key_pair(self):
        return SSHKeyPair(self.ssh_dir)

    def _ensure_dir(self, dname):
        if not os.access(dname, os.F_OK):
            self.log.info("Creating directory %s", dname)
            os.makedirs(dname)
        return dname

    def _ensure_cfssl_bin(self, prog):
        fname = os.path.join(self.bin_dir, prog)
        if not os.access(fname, os.F_OK):
            url = ("https://pkg.cfssl.org/R1.1/%s_%s-amd64" %
                   (prog, platform.system().lower()))
            self.log.info("Downloading %s to %s", url, fname)
            with closing(requests.get(url, stream=True)) as res:
                res.raise_for_status()
                try:
                    with open(fname, "wb") as f:
                        for chunk in res.iter_content(64 * 1024):
                            f.write(chunk)
                except:
                    try:
                        os.unlink(fname)
                    except:
                        pass
                    raise
            os.chmod(fname, 0o755)
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

    def _ensure_ssh_key(self):
        fname = self._key_file_name
        with self.ssh_keygen_lock:
            if not os.access(fname, os.R_OK):
                self.log.info("Generating SSH key pair %s", fname)
                utils.run("ssh-keygen -f %s -N ''" % (fname,))
        return fname


def make_discovery_token(size):
    res = requests.get("https://discovery.etcd.io/new?size=%d" % (size,))
    res.raise_for_status()
    return res.content[len("https://discovery.etcd.io/"):]
