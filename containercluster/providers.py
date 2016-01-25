from containercluster import core

from containercluster import digitalocean


core.register_provider("digitalocean", digitalocean.DigitalOceanProvider())
