import logging
import tempfile

from libcloud.compute.base import (KeyPair, Node, NodeImage, NodeLocation,
                                   NodeSize)
from libcloud.compute.types import NodeState

import mockssh

from containercluster import core


__all__ = []


class MockDriver(object):

    name = type = "mock"

    def __init__(self):
        self._key_pairs = {}
        self._nodes = {}

    def create_node(self, **kwargs):
        name = kwargs["name"]
        if name in self._nodes:
            raise Exception("Node '%s' already exists" % (name,))
        kwargs["driver"] = self
        self._nodes[name] = node = Node(**kwargs)
        return node

    def destroy_node(self, node):
        del self._nodes[node.name]

    def list_nodes(self):
        return self._nodes.values()

    def list_sizes(self):
        return [
            NodeSize("512mb", "512mb", 512, 0, 0, 0, None),
            NodeSize("1gb", "1gb", 1024, 0, 0, 0, None),
        ]

    def list_locations(self):
        return [
            NodeLocation("lon1", "lon1", "UK", None)
        ]

    def list_key_pairs(self):
        return self._key_pairs.values()

    def create_key_pair(self, name, ssh_key_pub):
        if name in self._key_pairs:
            raise Exception("Key pair '%s' already exists" % (name,))
        self._key_pairs[name] = k = KeyPair(name, "XXXX", "XXXX", self)
        return k

    def wait_until_running(self, nodes, **kwargs):
        return [(n, n.public_ips) for n in nodes]


class MockProvider(core.Provider):

    name = "mockprovider"

    default_etcd_size = "512mb"

    default_worker_size = "1gb"

    default_location = "lon1"

    driver = MockDriver()

    log = logging.getLogger(__name__)

    def __init__(self):
        super(MockProvider, self).__init__()
        self.ssh_server = mockssh.Server({})

    def provision_node(self, node):
        self.log.debug("MockProvider: Entering provision_node()")
        uid = node.ssh_uid
        private_key_path = node.config.ssh_key_pair.private_key_path
        self.log.debug("Adding SSH key %s for user '%s' to mock server",
                       private_key_path, uid)
        self.ssh_server.add_user(uid, private_key_path)

        # Patch node object for provisioning
        node.ssh_port = self.ssh_server.port
        node.certs_dir = tempfile.mkdtemp()
        node.sudo_cmd = ""

        return super(MockProvider, self).provision_node(node)

    def create_node(self, name, size, channel, location, ssh_key_id,
                    cloud_config_data):
        self.log.debug("MockProvider: Entering create_node(%s)", name)
        location = self.get_location(location)
        extra = {
            "location": location,
            "ssh_keys": [ssh_key_id],
        }
        if cloud_config_data:
            extra["user_data"] = cloud_config_data,

        node = self.driver.create_node(id=name,
                                       name=name,
                                       state=NodeState.RUNNING,
                                       public_ips=["127.0.0.1"],
                                       private_ips=[],
                                       size=self.get_size(size),
                                       image=self.get_image(channel),
                                       extra=extra)
        self.log.info("Node %s created", name)
        return node

    def get_image(self, channel):
        return NodeImage(channel, channel, self)


core.register_provider("mockprovider", MockProvider())
