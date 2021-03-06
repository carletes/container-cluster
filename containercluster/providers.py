import importlib
import logging
import threading


__all__ = [
    "Provider",
    "default_provider",
    "get_provider",
    "provider_names",
]


PROVIDERS = {
    "digitalocean": ("containercluster.digitalocean", "DigitalOceanProvider"),
}


def provider_names():
    return sorted(PROVIDERS.keys())


def default_provider():
    return get_provider("digitalocean")


def get_provider(name):
    try:
        mod_name, class_name = PROVIDERS[name]
    except KeyError:
        raise ValueError("Invalid provider `%s`" % (name,))
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise Exception("Invalid provider %s: Module %s has no attribute %s" %
                        (name, mod, class_name))
    return cls()


class Provider(object):

    create_key_pair_lock = threading.Lock()

    log = logging.getLogger(__name__)

    def __init__(self):
        self.sizes = {}
        self.images = {}
        self.locations = {}
        self._node_objs = {}

    def ensure_node(self, name, node_class, size, cluster, config):
        cluster_config = config.clusters[cluster.name]
        channel = cluster_config["channel"]
        location = cluster_config["location"]
        self.log.debug("Creating node %s (%s, %s, %s, %s)", name, node_class,
                       size, channel, location)
        node = node_class(name, self, cluster, config)

        for n in self.driver.list_nodes():
            if n.name == name:
                self.log.debug("Node '%s' already created", name)
                self.register_node(name, n)
                break
        else:
            channels = {"stable", "beta", "alpha"}
            if channel not in channels:
                raise ValueError("Unsupported CoreOS chanel '%s'."
                                 "Valid values: %s" %
                                 (channel, sorted(channels)))

            public_ssh_key = self.get_public_ssh_key(config.ssh_key_pair)
            n = self.create_node(name, size, channel, location,
                                 public_ssh_key.fingerprint,
                                 node.cloud_config_data)
            self.register_node(name, n)
            self.wait_until_running(n)

        return node

    def destroy_node(self, node):
        n = self._node_objs[node.name]
        self.log.debug("Destroying node '%s'", node.name)
        try:
            n.destroy()
        except:
            self.log.warn("Cannot destroy node %s", node.name, exc_info=True)
        del self._node_objs[node.name]

    def register_node(self, name, node_driver_obj):
        self._node_objs[name] = node_driver_obj

    def provision_node(self, node):
        return node.provision()

    def reboot_node(self, node):
        self.log.debug("Rebooting node '%s'", node.name)
        self._node_objs[node.name].reboot()

    def list_nodes(self):
        return self.driver.list_nodes()

    def wait_until_running(self, *nodes):
        res = self.driver.wait_until_running(self._node_objs[n.name]
                                             for n in nodes)
        for node, _ in res:
            self._node_objs[node.name] = node

    def node_state(self, node):
        return self._node_objs[node.name].state

    def node_public_ips(self, node):
        return self._node_objs[node.name].public_ips

    def node_private_ips(self, node):
        return self._node_objs[node.name].private_ips

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
