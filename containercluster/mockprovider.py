import logging

from libcloud.compute.base import (KeyPair, Node, NodeImage, NodeLocation,
                                   NodeSize)
from libcloud.compute.types import NodeState

from containercluster import core


__all__ = []


class MockDriver(object):

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


class MockProvider(core.Provider):

    name = "mockprovider"

    default_etcd_size = "512mb"

    default_worker_size = "1gb"

    default_location = "lon1"

    driver = MockDriver()

    log = logging.getLogger(__name__)

    def __init__(self):
        super(MockProvider, self).__init__()

    def create_node(self, name, size, channel, location, ssh_key_id,
                    cloud_config_data):
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
                                       public_ips=[],
                                       private_ips=[],
                                       size=self.get_size(size),
                                       image=self.get_image(channel),
                                       extra=extra)
        self.log.info("Node %s created", name)
        return node

    def get_image(self, channel):
        return NodeImage(channel, channel, self)


core.register_provider("mockprovider", MockProvider())
