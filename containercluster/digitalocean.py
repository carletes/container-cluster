import logging
import os

from libcloud.compute.types import NodeState, Provider
from libcloud.compute.providers import get_driver

from containercluster import providers


__all__ = []


class DigitalOceanProvider(providers.Provider):

    name = "digitalocean"

    default_location = "lon1"

    default_etcd_size = "512mb"

    default_worker_size = "512mb"

    log = logging.getLogger(__name__)

    def __init__(self):
        super(DigitalOceanProvider, self).__init__()

    def create_node(self, name, size, channel, location, ssh_key_id,
                    cloud_config_data):
        node = self.driver.create_node(name,
                                       self.get_size(size),
                                       self.get_image(channel),
                                       self.get_location(location),
                                       ex_create_attr={
                                           "backups": False,
                                           "ipv6": False,
                                           "private_networking": True,
                                           "ssh_keys": [ssh_key_id],
                                       },
                                       ex_user_data=cloud_config_data)
        self.log.debug("Node %s created", name)
        return node

    def reboot_node(self, node):
        if node.state == NodeState.STOPPED:
            self.log.debug("Powering on node '%s'", node.name)
            self.driver.ex_power_on_node(node)
        else:
            return super(DigitalOceanProvider, self).reboot_node(node)

    def get_image(self, channel):
        try:
            return self.images[channel]
        except KeyError:
            for img in self.driver.list_images():
                if img.extra["distribution"] == "CoreOS":
                    if channel in img.name:
                        self.images[channel] = img
                        return img
        raise Exception("Cannot find CoreOS image for channel '%s'" %
                        (channel,))

    @property
    def driver(self):
        try:
            token = os.environ["DIGITALOCEAN_ACCESS_TOKEN"]
        except KeyError as e:
            raise Exception("Environment variable '%s' not set" % (e.message,))
        return get_driver(Provider.DIGITAL_OCEAN)(token, api_version="v2")
