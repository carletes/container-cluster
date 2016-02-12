import logging
import json
import os
import subprocess
import tempfile
import urlparse

from libcloud.compute.types import NodeState

from paramiko.client import MissingHostKeyPolicy, SSHClient

from containercluster import utils


__all__ = [
    "cluster_env",
    "create_cluster",
    "destroy_cluster",
    "provision_cluster",
    "start_cluster",
]


class Node(object):

    node_type = None

    ssh_uid = "core"
    ssh_port = 22
    sudo_cmd = "sudo"
    certs_dir = "/home/core/tls"

    log = logging.getLogger(__name__)

    def __init__(self, name, provider, cluster, config):
        self.name = name
        self.provider = provider
        self.cluster = cluster
        self.config = config

    def provision(self, vars):
        self.log.info("Provisioning node %s", self.name)
        self.provider.wait_until_running(self)

        ssh_host = self.public_ips[0]
        self.log.debug("Waiting for SSH on %s:%d", ssh_host, self.ssh_port)
        utils.wait_for_port_open(ssh_host, self.ssh_port, check_interval=1.0)

        with self.ssh_session as s:
            s.exec_command("%s mkdir -p %s" % (self.sudo_cmd, self.certs_dir))
            s.exec_command("%s chown -R %s %s" %
                           (self.sudo_cmd, self.ssh_uid, self.certs_dir))
            try:
                sftp = s.open_sftp()
                sftp.put(self.config.ca_cert_path,
                         os.path.join(self.certs_dir, "ca.pem"))
                sftp.put(self.tls_cert_path,
                         os.path.join(self.certs_dir, "node.pem"))
                sftp.put(self.tls_key_path,
                         os.path.join(self.certs_dir, "node-key.pem"))
            finally:
                s.exec_command("%s chown -R root: %s" %
                               (self.sudo_cmd, self.certs_dir))

    def destroy(self):
        for fname in self.tls_paths:
            self.log.debug("Removing %s", fname)
            try:
                os.unlink(fname)
            except OSError:
                self.log.warn("Cannot remove %s", fname, exc_info=True)

    def state(self):
        return self.provider.node_state(self)

    @property
    def tls_paths(self):
        return [self.tls_cert_path, self.tls_key_path]

    @property
    def public_ips(self):
        return self.provider.node_public_ips(self)

    @property
    def private_ips(self):
        return self.provider.node_private_ips(self)

    @property
    def cloud_config_template(self):
        fname = os.path.join(os.path.dirname(__file__),
                             "%s-cloud-config.yaml" % (self.node_type,))
        if not os.access(fname, os.F_OK):
            raise Exception("No cloud-config template for node type '%s'" %
                            (self.node_type,))

        with open(fname, "rt") as f:
            return f.read()

    @property
    def cloud_config_vars(self):
        cluster = self.config.clusters[self.cluster.name]
        return {
            "certs_dir": self.certs_dir,
            "node_name": self.name,
            "discovery_token": cluster["discovery_token"],
            "network_config": json.dumps(
                {
                    "Network": str(cluster["network"]),
                    "SubnetLen": cluster["subnet_length"],
                    "SubnetMin": str(cluster["subnet_min"].network_address),
                    "SubnetMax": str(cluster["subnet_max"].network_address),
                    "Backend": {
                        "Type": "vxlan",
                        "VNI": 1,
                        "Port": 8472,
                    }
                }),
        }

    @property
    def cloud_config_data(self):
        return self.cloud_config_template % self.cloud_config_vars

    @property
    def ssh_session(self):
        private_key_path = self.config.ssh_key_pair.private_key_path
        self.log.debug("Using SSH private key %s for user '%s'",
                       private_key_path, self.ssh_uid)
        return SshSession(uid=self.ssh_uid,
                          addr=self.public_ips[0],
                          port=self.ssh_port,
                          private_key_path=private_key_path)

    @property
    def tls_cert_path(self):
        cert_fname, _ = self._ensure_tls()
        return cert_fname

    @property
    def tls_cert(self):
        with open(self.tls_cert_path, "rt") as f:
            return f.read()

    @property
    def tls_key_path(self):
        _, key_fname = self._ensure_tls()
        return key_fname

    @property
    def tls_key(self):
        with open(self.tls_key_path, "rt") as f:
            return f.read()

    def _ensure_tls(self):
        alt_names = [u"127.0.0.1"]
        alt_names.extend(unicode(ip) for ip in self.public_ips)
        alt_names.extend(unicode(ip) for ip in self.private_ips)
        return self.config.node_tls_paths(unicode(self.name), alt_names)


class EtcdNode(Node):

    node_type = "etcd"


class WorkerNode(Node):

    node_type = "worker"

    @property
    def cloud_config_vars(self):
        vars = dict(super(WorkerNode, self).cloud_config_vars)
        vars["etcd_endpoint"] = self.cluster.etcd_endpoint
        return vars


class MasterNode(Node):

    node_type = "master"

    @property
    def cloud_config_vars(self):
        cluster = self.config.clusters[self.cluster.name]
        etcd_url = urlparse.urlparse(self.cluster.etcd_endpoint.split(",")[0])
        etcd_host, etcd_port = etcd_url.netloc.split(":")
        vars = dict(super(MasterNode, self).cloud_config_vars)
        vars.update({
            "cluster_name": self.cluster.name,
            "dns_service_ip": str(cluster["dns_service_ip"]),
            "etcd_endpoint": self.cluster.etcd_endpoint,
            "etcd_endpoint_list": ", ".join('"%s"' % (e,) for e in self.cluster.etcd_endpoint.split(",")),
            "etcd_endpoint_host": etcd_host,
            "etcd_endpoint_port": etcd_port,
            "kubernetes_version": "v1.1.2",
            "services_ip_range": str(cluster["services_ip_range"]),
        })
        return vars

    def provision(self, vars):
        super(MasterNode, self).provision(vars)

        with self.ssh_session as s:
            s.exec_command("%s chown -R %s %s" %
                           (self.sudo_cmd, self.ssh_uid, self.certs_dir))
            try:
                sftp = s.open_sftp()
                sftp.put(self.apiserver_cert_path,
                         os.path.join(self.certs_dir, "apiserver.pem"))
                sftp.put(self.apiserver_key_path,
                         os.path.join(self.certs_dir, "apiserver-key.pem"))
            finally:
                s.exec_command("%s chown -R root: %s" %
                               (self.sudo_cmd, self.certs_dir))

    @property
    def tls_paths(self):
        return (super(MasterNode, self).tls_paths +
                [self.apiserver_cert_path, self.apiserver_key_path])

    @property
    def apiserver_cert_path(self):
        cert_fname, _ = self._ensure_apiserver_tls()
        return cert_fname

    @property
    def apiserver_cert(self):
        with open(self.apiserver_cert_path, "rt") as f:
            return f.read()

    @property
    def apiserver_key_path(self):
        _, key_fname = self._ensure_apiserver_tls()
        return key_fname

    @property
    def apiserver_key(self):
        with open(self.apiserver_key_path, "rt") as f:
            return f.read()

    def _ensure_apiserver_tls(self):
        cluster = self.config.clusters[self.cluster.name]
        alt_names = [
            u"kubernetes",
            u"kubernetes.default",
            u"kubernetes.default.svc",
            u"kubernetes.default.svc.%s.local" % (self.cluster.name,),
            unicode(cluster["kubernetes_service_ip"]),
        ]
        alt_names.extend(unicode(ip) for ip in self.public_ips)
        alt_names.extend(unicode(ip) for ip in self.private_ips)
        return self.config.node_tls_paths(u"kube-apiserver", alt_names)


NODE_TYPES = dict((cls.node_type, cls) for cls in
                  (EtcdNode, MasterNode, WorkerNode))


def make_etcd_endpoint(nodes):
    return ",".join("https://%s:2379" % (n.public_ips[0],) for n in nodes)


class Cluster(object):

    log = logging.getLogger(__name__)

    def __init__(self, name, provider, config):
        self.name = name
        self.provider = provider
        self.config = config
        self._nodes = []
        self._node_names = None
        self._etcd_endpoint = None

    @property
    def nodes(self):
        if not self._nodes:
            self.log.info("Creating nodes for cluster '%s'", self.name)
            nodes_data = self.config.clusters[self.name]["nodes"]

            # Start first the `etcd` nodes, since the etcd endpoint is needed in
            # all other nodes, and it will not be known until the `etcd` nodes
            # are up.
            self._nodes = utils.parallel((self.provider.ensure_node,
                                          n["name"],
                                          NODE_TYPES[n["type"]],
                                          n["size"],
                                          self,
                                          self.config)
                                         for n in nodes_data
                                         if n["type"] == "etcd")
            self._etcd_endpoint = make_etcd_endpoint(self._nodes)

            # Start now all non-etcd nodes.
            self._nodes.extend(utils.parallel((self.provider.ensure_node,
                                               n["name"],
                                               NODE_TYPES[n["type"]],
                                               n["size"],
                                               self,
                                               self.config)
                                              for n in nodes_data
                                              if n["type"] != "etcd"))
        return self._nodes

    @property
    def etcd_nodes(self):
        ret = []
        for n in self.nodes:
            self.log.debug("etcd_nodes(): Examining %s", n)
            if isinstance(n, EtcdNode):
                ret.append(n)
        self.log.debug("etcd_nodes(): Returning %s", ret)
        return ret

    @property
    def exisiting_nodes(self):
        ret = []
        driver_nodes = dict((n.name, n) for n in self.provider.list_nodes())
        for n in self.config.clusters[self.name]["nodes"]:
            name = n["name"]
            if name in driver_nodes:
                node_class = NODE_TYPES[n["type"]]
                node = node_class(name, self.provider, self, self.config)
                self.provider.register_node(name, driver_nodes[name])
                ret.append(node)
        return ret

    @property
    def node_names(self):
        if self._node_names is None:
            self._node_names = {n["name"]for n in self.config.clusters[self.name]["nodes"]}
        return self._node_names

    def destroy_nodes(self):
        for n in self.exisiting_nodes:
            self.log.debug("Destroying node '%s'", n.name)
            n.destroy()
            self.provider.destroy_node(n)

    def start_nodes(self):
        self.log.info("Starting nodes for cluster '%s'", self.name)

        def restart_if_needed(node):
            if node.state == NodeState.RUNNING:
                self.log.info("Node '%s' running", node.name)
                return

            if node.state == NodeState.REBOOTING:
                self.log.info("Node '%s' rebooting", node.name)
                return

            if node.state == NodeState.PENDING:
                self.log.info("Node '%s' is being created", node.name)
                return

            error_states = {
                NodeState.TERMINATED,
                NodeState.ERROR,
                NodeState.UNKNOWN
            }

            if node.state in error_states:
                raise Exception("Node '%s' cannot be restarted" % (node.name,))

            self.log.info("Rebooting node '%s'", node.name)
            self.provider.reboot_node(node.driver_obj)

        utils.parallel((restart_if_needed, node) for node in self.nodes)
        self.log.info("%s: Waiting for nodes ...", self.name)
        nodes = self.provider.wait_until_running(*self.nodes)
        self.log.debug("start_nodes(): Nodes up: %s", nodes)

    def provision_nodes(self):
        vars = {
            "etcd_endpoint": self.etcd_endpoint,
        }
        utils.parallel((self.provider.provision_node, n, vars)
                       for n in self.nodes)

    @property
    def etcd_endpoint(self):
        if self._etcd_endpoint is None:
            self._etcd_endpoint = make_etcd_endpoint(self.nodes)
        return self._etcd_endpoint

    @property
    def env_variables(self):
        return (
            ("ETCDCTL_CA_FILE", self.config.ca_cert_path),
            ("ETCDCTL_CERT_FILE", self.config.admin_cert_path),
            ("ETCDCTL_KEY_FILE", self.config.admin_key_path),
            ("ETCDCTL_ENDPOINT", self.etcd_endpoint),
        )


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


LOG = logging.getLogger(__name__)


def create_cluster(name, channel, n_etcd, size_etcd, n_workers, size_worker,
                   provider, location, network, subnet_length, subnet_min,
                   subnet_max,  services_ip_range, dns_service_ip,
                   kubernetes_service_ip, config):
    LOG.info("Creating cluster %s", name)
    config.add_cluster(name, channel, n_etcd, size_etcd,
                       n_workers, size_worker, provider, location, network,
                       subnet_length, subnet_min, subnet_max, services_ip_range,
                       dns_service_ip, kubernetes_service_ip)
    config.save()
    return Cluster(name, provider, config)


def provision_cluster(name, provider, config):
    LOG.info("Provisioning cluster '%s'", name)
    cluster = Cluster(name, provider, config)
    return cluster.provision_nodes()


def destroy_cluster(name, provider, config):
    LOG.info("Destroying cluster '%s'", name)
    cluster = Cluster(name, provider, config)
    cluster.destroy_nodes()
    config.remove_cluster(name)
    config.save()


def start_cluster(name, provider, config):
    cluster = Cluster(name, provider, config)
    return cluster.start_nodes()


def cluster_env(name, provider, config):
    cluster = Cluster(name, provider, config)
    return sorted(cluster.env_variables)


def ssh_session(name, provider, config):
    cluster = Cluster(name, provider, config)
    fd, path = tempfile.mkstemp()
    for node in sorted(cluster.nodes, key=lambda node: node.name):
        os.write(fd, ("screen -t %s ssh -i %s "
                      "-o UserKnownHostsFile=/dev/null "
                      "-o StrictHostKeyChecking=no "
                      "core@%s\n") %
                 (node.name, config.ssh_key_pair.private_key_path,
                  node.public_ips[0]))
    p = subprocess.Popen("screen -S %s -c %s" % (cluster.name, path),
                         shell=True)
    p.wait()
