import logging
import os
import threading
import time

from libcloud.compute.types import NodeState

from paramiko.client import MissingHostKeyPolicy, SSHClient

from containercluster import utils


__all__ = [
    "Provider",
    "create_cluster",
    "default_provider",
    "provider_names",
    "register_provider",
    "start_cluster",
]


PROVIDERS = {}


def register_provider(name, impl):
    PROVIDERS[name] = impl


def provider_names():
    return sorted(PROVIDERS.keys())


def default_provider():
    return PROVIDERS["digitalocean"]


class Node(object):

    node_type = None

    ssh_uid = "core"
    ssh_port = 22
    sudo_cmd = "sudo"
    certs_dir = "/home/core"

    log = logging.getLogger(__name__)

    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.driver_obj = None

    def reboot(self):
        return self.driver_obj.reboot()

    def wait_until_running(self):
        node = self.driver_obj
        self.log.info("Waiting for node %s ...", self.name)
        nodes = self.driver_obj.driver.wait_until_running([node])
        self.log.debug("Node %s up: %s", self.name, nodes)
        self.driver_obj = nodes[0][0]

    def provision(self):
        self.log.info("Provisioning node %s", self.name)
        self.wait_until_running()
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
        return self.driver_obj.state

    @property
    def public_ip(self):
        return self.driver_obj.public_ips[0]

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
    def ssh_session(self):
        private_key_path = self.config.ssh_key_pair.private_key_path
        self.log.debug("Using SSH private key %s for user '%s'",
                       private_key_path, self.ssh_uid)
        return SshSession(uid=self.ssh_uid,
                          addr=self.public_ip,
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
        alt_names.extend(unicode(ip) for ip in self.driver_obj.public_ips)
        alt_names.extend(unicode(ip) for ip in self.driver_obj.private_ips)
        return self.config.node_tls_paths(unicode(self.name), alt_names)


class EtcdNode(Node):

    node_type = "etcd"


class WorkerNode(Node):

    node_type = "worker"


NODE_TYPES = dict((cls.node_type, cls) for cls in (EtcdNode, WorkerNode))


class Provider(object):

    create_key_pair_lock = threading.Lock()

    log = logging.getLogger()

    def __init__(self):
        self.sizes = {}
        self.images = {}
        self.locations = {}

    def ensure_node(self, name, node_type, size, cluster_name, config):
        cluster = config.clusters[cluster_name]
        channel = cluster["channel"]
        location = cluster["location"]
        self.log.info("Creating node %s (%s, %s, %s, %s)", name, node_type,
                      size, channel, location)
        node = NODE_TYPES[node_type](name, config)

        for n in self.driver.list_nodes():
            if n.name == name:
                self.log.info("Node '%s' already created", name)
                node.driver_obj = n
                break
        else:
            channels = {"stable", "beta", "alpha"}
            if channel not in channels:
                raise ValueError("Unsupported CoreOS chanel '%s'."
                                 "Valid values: %s" %
                                 (channel, sorted(channels)))

            public_ssh_key = self.get_public_ssh_key(config.ssh_key_pair)
            try:
                cloud_config_data = node.cloud_config_template % cluster
            except:
                cloud_config_data = None
            n = self.create_node(name, size, channel, location,
                                 public_ssh_key.fingerprint,
                                 cloud_config_data)
            node.driver_obj = n

            # Try waiting a bit before deploying
            self.log.debug("Waiting a bit before provisioning %s", node)
            time.sleep(5.0)

            self.provision_node(node)

        return node

    def provision_node(self, node):
        self.log.debug("provision_node(): Calling node.provision()")
        return node.provision()

    def reboot_node(self, node):
        self.log.info("Rebooting node '%s'", node.name)
        node.reboot()

    def list_nodes(self):
        return self.driver.list_nodes()

    def get_public_ssh_key(self, ssh_key_pair):
        with self.create_key_pair_lock:
            for k in self.driver.list_key_pairs():
                if k.name == ssh_key_pair.name:
                    return k
            return self.driver.create_key_pair(ssh_key_pair.name,
                                               ssh_key_pair.public_key)

    def get_size(self, name):
        if not self.sizes:
            self.sizes.update(dict((s.name, s)
                                   for s in self.driver.list_sizes()))
        try:
            return self.sizes[name]
        except KeyError:
            msg = ("Unsupported size '%s'. Valid values: %s" %
                   (name, ",".join(sorted(self.sizes.keys()))))
            raise ValueError(msg)

    def get_location(self, name):
        if not self.locations:
            self.locations.update(dict((l.id, l)
                                       for l in self.driver.list_locations()))
        try:
            return self.locations[name]
        except KeyError:
            raise ValueError("Unsupported location '%s'. Valid values: %s" %
                             (name, ", ".join(sorted(self.locations.keys()))))

    @property
    def name(self):
        raise NotImplementedError("name")

    @property
    def default_location(self):
        raise NotImplementedError("default_location")

    @property
    def default_etcd_size(self):
        raise NotImplementedError("default_etcd_size")

    @property
    def default_worker_size(self):
        raise NotImplementedError("default_worker_size")

    @property
    def driver(self):
        raise NotImplementedError("driver")

    def create_node(self, name, size, channel, location, ssh_key_id,
                    cloud_config_data):
        raise NotImplementedError("create_node")

    def get_image(self, channel):
        raise NotImplementedError("get_image")


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
            self._nodes = utils.parallel((self.provider.ensure_node,
                                          n["name"],
                                          n["type"],
                                          n["size"],
                                          self.name,
                                          self.config)
                                         for n in nodes_data)
        return self._nodes

    @property
    def exisiting_nodes(self):
        ret = []
        for n in self.provider.list_nodes():
            if n.name in self.node_names:
                ret.append(n)
        return ret

    @property
    def node_names(self):
        if self._node_names == None:
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
        nodes = self.provider.driver.wait_until_running(n.driver_obj
                                                        for n in self.nodes)
        self.log.debug("start_nodes(): Nodes up: %s", nodes)

    def provision_nodes(self):
        utils.parallel((self.provider.provision_node, n) for n in self.nodes)

    @property
    def env_variables(self):
        return (
            ("ETCDCTL_CA_FILE", self.config.ca_cert_path),
            ("ETCDCTL_CERT_FILE", self.config.admin_cert_path),
            ("ETCDCTL_KEY_FILE", self.config.admin_key_path),
            ("ETCDCTL_ENDPOINT", ",".join("https://%s:2379" % n.public_ip
                                          for n in self.nodes
                                          if n.node_type == "etcd")),
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
    try:
        provider_impl = PROVIDERS[provider]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (provider,))

    config.add_cluster(name, channel, n_etcd, size_etcd,
                       n_workers, size_worker, provider, location)
    config.save()

    return Cluster(name, provider_impl, config)


def provision_cluster(name, config):
    LOG.info("Provisioning cluster '%s'", name)
    provider_name = config.clusters[name]["provider"]
    try:
        provider_impl = PROVIDERS[provider_name]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (provider_name,))
    cluster = Cluster(name, provider_impl, config)
    return cluster.provision_nodes()


def destroy_cluster(name, config):
    LOG.info("Destroying cluster '%s'", name)
    provider_name = config.clusters[name]["provider"]
    try:
        provider_impl = PROVIDERS[provider_name]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (provider_name,))
    cluster = Cluster(name, provider_impl, config)
    cluster.destroy_nodes()
    config.remove_cluster(name)
    config.save()


def start_cluster(name, config):
    LOG.info("Starting cluster %s", name)
    provider_name = config.clusters[name]["provider"]
    try:
        provider_impl = PROVIDERS[provider_name]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (provider_name,))

    cluster = Cluster(name, provider_impl, config)
    return cluster.start_nodes()


def cluster_env(name, config):
    provider_name = config.clusters[name]["provider"]
    try:
        provider_impl = PROVIDERS[provider_name]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (provider_name,))

    cluster = Cluster(name, provider_impl, config)
    return sorted(cluster.env_variables)
