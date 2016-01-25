import logging
import os
import threading

from libcloud.compute.types import NodeState

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

    def __init__(self, name):
        self.name = name
        self.driver_obj = None

    def destroy(self):
        return self.driver_obj.destroy()

    def reboot(self):
        return self.driver_obj.reboot()

    @property
    def state(self):
        return self.driver_obj.state

    @property
    def cloud_config_template(self):
        fname = os.path.join(os.path.dirname(__file__),
                             "%s-cloud-config.yaml" % (self.node_type,))
        if not os.access(fname, os.F_OK):
            raise Exception("No cloud-config template for node type '%s'" %
                            (self.node_type,))

        with open(fname, "rt") as f:
            return f.read()


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

    def ensure_node(self, name, node_type, size, cluster, ssh_key_pair):
        channel = cluster["channel"]
        location = cluster["location"]
        self.log.info("Creating node %s (%s, %s, %s, %s)", name, node_type,
                      size, channel, location)
        node = NODE_TYPES[node_type](name)

        for n in self.driver.list_nodes():
            if n.name == name:
                self.log.info("Node '%s' already created", name)
                node.driver_obj = n
                return node

        channels = {"stable", "beta", "alpha"}
        if channel not in channels:
            raise ValueError("Unsupported CoreOS chanel '%s'."
                             "Valid values: %s" %
                             (channel, sorted(channels)))

        public_ssh_key = self.get_public_ssh_key(ssh_key_pair)
        try:
            cloud_config_data = node.cloud_config_template % cluster
        except:
            cloud_config_data = None
        n = self.create_node(name, size, channel, location,
                             public_ssh_key.fingerprint,
                             cloud_config_data)
        node.driver_obj = n
        return node

    def destroy_node(self, node):
        self.log.info("Destroying node '%s'", node.name)
        node.destroy()

    def reboot_node(self, node):
        self.log.info("Rebooting node '%s'", node.name)
        node.reboot()

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

    @property
    def nodes(self):
        if not self._nodes:
            self.log.info("Creating nodes for cluster '%s'", self.name)
            cluster_data = self.config.clusters[self.name]
            nodes_data = cluster_data["nodes"]
            self._nodes = utils.parallel(*[(self.provider.ensure_node,
                                            n["name"],
                                            n["type"],
                                            n["size"],
                                            cluster_data,
                                            self.config.ssh_key_pair)
                                           for n in nodes_data])
        return self._nodes

    def destroy_nodes(self):
        utils.parallel(*[(self.provider.destroy_node, n.driver_obj)
                         for n in self.nodes])

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

        utils.parallel(*[(restart_if_needed, node) for node in self.nodes])
        self.log.info("%s: Waiting for nodes ...", self.name)
        nodes = self.provider.driver.wait_until_running(n.driver_obj
                                                        for n in self.nodes)
        self.log.debug("start_nodes(): Nodes up: %s", nodes)


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
