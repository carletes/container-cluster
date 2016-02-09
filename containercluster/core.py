import logging
import os
import tempfile

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
    certs_dir = "/home/core"

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
        utils.wait_for_port_open(ssh_host, self.ssh_port, timeout=60.0)

        with self.ssh_session as s:
            s.exec_command("mkdir -p %s" % (self.certs_dir,))
            sftp = s.open_sftp()
            sftp.put(self.config.ca_cert_path,
                     os.path.join(self.certs_dir, "ca.pem"))
            sftp.put(self.tls_cert_path,
                     os.path.join(self.certs_dir, "node.pem"))
            sftp.put(self.tls_key_path,
                     os.path.join(self.certs_dir, "node-key.pem"))

    @property
    def state(self):
        return self.provider.node_state(self)

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
        token = self.config.clusters[self.cluster.name]["discovery_token"]
        return {
            "node_name": self.name,
            "discovery_token": token,
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


WORKER_PROFILE_TEMPLATE = """
export ETCDCTL_ENDPOINT=%(etcd_endpoint)s
""".lstrip()


class WorkerNode(Node):

    node_type = "worker"

    def provision(self, vars):
        fd, path = tempfile.mkstemp()
        os.write(fd, WORKER_PROFILE_TEMPLATE % vars)
        os.close(fd)
        with self.ssh_session as s:
            s.exec_command("%s mkdir -p /etc/profile.d" % (self.sudo_cmd,))
            s.exec_command("%s chown -R %s /etc/profile.d" %
                           (self.sudo_cmd, self.ssh_uid))
            try:
                sftp = s.open_sftp()
                sftp.put(path, "/etc/profile.d/20-worker-node.sh")
            finally:
                s.exec_command("%s chown -R root: /etc/profile.d" %
                               (self.sudo_cmd,))

        super(WorkerNode, self).provision(vars)


NODE_TYPES = dict((cls.node_type, cls) for cls in (EtcdNode, WorkerNode))


class Cluster(object):

    log = logging.getLogger(__name__)

    def __init__(self, name, provider, config):
        self.name = name
        self.provider = provider
        self.config = config
        self._nodes = []
        self._node_names = None

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
        for n in self.provider.list_nodes():
            if n.name in self.node_names:
                ret.append(n)
        return ret

    @property
    def node_names(self):
        if self._node_names is None:
            self._node_names = {n["name"]for n in self.config.clusters[self.name]["nodes"]}
        return self._node_names

    def destroy_nodes(self):
        for n in self.exisiting_nodes:
            self.log.debug("Destroying node '%s'", n.name)
            try:
                n.destroy()
            except:
                self.log.warn("Cannot destroy node %s", n.name, exc_info=True)
        for n in self.node_names:
            for f in self.config.node_tls_paths(unicode(n)):
                self.log.debug("Removing %s", f)
                try:
                    os.unlink(f)
                except OSError:
                    self.log.warn("Cannot remove %s", f)

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
        return ",".join("https://%s:2379" % (n.public_ips[0],)
                        for n in self.etcd_nodes)

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
                   provider, location, config):
    LOG.info("Creating cluster %s (channel: %s, etcd nodes: %d, etcd size: %s,"
             " worker nodes: %d, worker size: %s, provider: %s, location: %s)",
             name, channel, n_etcd, size_etcd, n_workers, size_worker,
             provider, location)
    config.add_cluster(name, channel, n_etcd, size_etcd,
                       n_workers, size_worker, provider, location)
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
