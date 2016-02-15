import argparse
import logging
import logging.config
import sys

import ipaddress

from containercluster import config, core

from containercluster.providers import (
    default_provider, get_provider, provider_names
)


__all__ = [
    "main",
]


def main():
    """Manages the life-cycle of CoreOS-based container clusters.

    """
    p = argparse.ArgumentParser(prog="container-cluster",
                                description=main.__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--quiet", action="store_true",
                   help="only display program errors", default=False)
    g.add_argument("--debug", action="store_true",
                   help="trace program execution", default=False)
    subp = p.add_subparsers()

    create_p = subp.add_parser("create", description=create_cluster.__doc__)
    create_p.add_argument("name", metavar="NAME", help="cluster name")
    create_p.add_argument("--channel", metavar="CHANNEL",
                          help="CoreOS channel (default: %(default)s)",
                          default="alpha")
    create_p.add_argument("--num-etcd", metavar="NUM", type=int,
                          help="number of etcd nodes (default: %(default)s)",
                          default=3)
    create_p.add_argument("--size-etcd", metavar="SIZE",
                          help="size for etc nodes (default: %(default)s)",
                          default=default_provider().default_etcd_size)
    create_p.add_argument("--num-workers", metavar="NUM", type=int,
                          help="number of worker nodes (default: %(default)s)",
                          default=2)
    create_p.add_argument("--size-workers", metavar="SIZE",
                          help="size for worker nodes (default: %(default)s)",
                          default=default_provider().default_worker_size)
    create_p.add_argument("--location", metavar="LOCATION",
                          help="instance location (default: %(default)s)",
                          default=default_provider().default_location)
    create_p.add_argument("--provider", metavar="PROVIDER",
                          help="cloud provider (default: %(default)s)",
                          choices=provider_names(),
                          default=default_provider().name)
    create_p.add_argument("--flannel-network", metavar="xxx.xxx.xxx.xxx/nn",
                          help="flannel network (default: %(default)s)",
                          default="172.16.0.0/16")
    create_p.add_argument("--flannel-subnet-length", metavar="N", type=int,
                          help="flannel subnet length (default: %(default)s)",
                          default=24)
    create_p.add_argument("--flannel-subnet-min", metavar="xxx.xxx.xxx.xxx",
                          help="minimum flannel subnet (default: %(default)s)",
                          default="172.16.1.0")
    create_p.add_argument("--flannel-subnet-max", metavar="xxx.xxx.xxx.xxx",
                          help="maximum flannel subnet (default: %(default)s)",
                          default="172.16.254.0")
    create_p.add_argument("--services-ip-range", metavar="xxx.xxx.xxx.xxx/nn",
                          help="subnet for Kubernetes services (default: %(default)s)",
                          default="172.17.0.0/24")
    create_p.add_argument("--dns-service-ip", metavar="xxx.xxx.xxx.xxx",
                          help=("virtual IP for Kubernetes cluster DNS service "
                                "(default: %(default)s)"),
                          default="172.17.0.10")
    create_p.add_argument("--kubernetes-service-ip", metavar="xxx.xxx.xxx.xxx",
                          help=("virtual IP for Kubernetes API "
                                "(default: %(default)s)"),
                          default="172.17.0.1")

    create_p.set_defaults(func=create_cluster)

    provision_p = subp.add_parser("provision",
                                  description=provision_cluster.__doc__)
    provision_p.add_argument("name", metavar="NAME", help="cluster name")
    provision_p.set_defaults(func=provision_cluster)

    destroy_p = subp.add_parser("destroy", description=destroy_cluster.__doc__)
    destroy_p.add_argument("name", metavar="NAME", help="cluster name")
    destroy_p.set_defaults(func=destroy_cluster)

    up_p = subp.add_parser("up", description=cluster_up.__doc__)
    up_p.add_argument("name", metavar="NAME", help="cluster name")
    up_p.set_defaults(func=cluster_up)

    down_p = subp.add_parser("down", description=cluster_down.__doc__)
    down_p.add_argument("name", metavar="NAME", help="cluster name")
    down_p.set_defaults(func=cluster_down)

    env_p = subp.add_parser("env", description=cluster_env.__doc__)
    env_p.add_argument("name", metavar="NAME", help="cluster name")
    env_p.set_defaults(func=cluster_env)

    ssh_p = subp.add_parser("ssh", description=ssh.__doc__)
    ssh_p.add_argument("name", metavar="NAME", help="cluster name")
    ssh_p.set_defaults(func=ssh)

    args = p.parse_args()

    if args.quiet:
        log_level = "quiet"
    elif args.debug:
        log_level = "debug"
    else:
        log_level = "normal"
    configure_logging(log_level)

    try:
        return args.func(args)
    except Exception as exc:
        logging.exception("Command `%s` failed: %s",
                          " ".join(sys.argv), exc.message)
        return 1


def create_cluster(args):
    """Create a cluster and start all its nodes.

    """
    conf = config.Config()
    if args.name in conf.clusters:
        logging.error("Cluster `%s` already exists", args.name)
        return 1

    network = ipaddress.ip_network(u"%s" % (args.flannel_network,))
    subnet_length = args.flannel_subnet_length
    subnet_min = ipaddress.ip_network(u"%s/%d" % (
        args.flannel_subnet_min, subnet_length
    ))
    subnet_max = ipaddress.ip_network(u"%s/%d" % (
        args.flannel_subnet_max, subnet_length
    ))
    services_ip_range = ipaddress.ip_network(u"%s" % (args.services_ip_range,))
    dns_service_ip = ipaddress.ip_address(u"%s" % (args.dns_service_ip,))
    kubernetes_service_ip = ipaddress.ip_address(u"%s" % (args.kubernetes_service_ip,))
    for net in (subnet_min, subnet_max):
        if net.prefixlen != subnet_length:
            logging.error("Network %s is not a /%d network", net, subnet_length)
            return 1
        if not net.subnet_of(network):
            logging.error("Network %s is not a subnet of %s", net, network)
            return 1
    if services_ip_range.overlaps(network):
        logging.error("Service IP range %s overlaps with network %s",
                      services_ip_range, network)
        return 1
    if dns_service_ip not in services_ip_range:
        logging.error("DNS service IP address %s not in service IP range %s",
                      dns_service_ip, services_ip_range)
        return 1
    if kubernetes_service_ip not in services_ip_range:
        logging.error("Kubernetes API IP address %s not in service IP range %s",
                      kubernetes_service_ip, services_ip_range)
        return 1

    provider = get_provider(args.provider)
    core.create_cluster(args.name, args.channel, args.num_etcd, args.size_etcd,
                        args.num_workers, args.size_workers, provider,
                        args.location, network, subnet_length, subnet_min,
                        subnet_max, services_ip_range, dns_service_ip,
                        kubernetes_service_ip, conf)
    cluster_up(args)
    return provision_cluster(args)


def provision_cluster(args):
    """Configures all nodes in a cluster.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        logging.error("Unknown cluster '%s'", args.name)
        return 1
    provider = conf.clusters[args.name]["provider"]
    return core.provision_cluster(args.name, provider, conf)


def destroy_cluster(args):
    """Destroy a cluster.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        logging.error("Unknown cluster '%s'", args.name)
        return 1
    provider = conf.clusters[args.name]["provider"]
    return core.destroy_cluster(args.name, provider, conf)


def cluster_up(args):
    """Ensure all nodes of an existing cluster are up.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        logging.error("Unknown cluster '%s'", args.name)
        return 1
    provider = conf.clusters[args.name]["provider"]
    return core.start_cluster(args.name, provider, conf)


def cluster_down(args):
    """Ensure all nodes of an existing cluster are down.

    """


def cluster_env(args):
    """Prints the command-line environment for accessing a cluster.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        logging.error("Unknown cluster '%s'", args.name)
        return 1
    provider = conf.clusters[args.name]["provider"]
    for k, v in core.cluster_env(args.name, provider, conf):
        print "export %s=%s" % (k, v)


def ssh(args):
    """Open a `screen(1)` session connected to all cluster nodes.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        logging.error("Unknown cluster '%s'", args.name)
        return 1
    provider = conf.clusters[args.name]["provider"]
    return core.ssh_session(args.name, provider, conf)


def configure_logging(level):
    config = {
        "version": 1,
        "formatters": {
            "brief": {
                "format": "%(message)s"
            },
            "detailed": {
                "format": "%(asctime)s %(threadName)s %(name)s %(message)s"
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "level": None,
                "formatter": None,
            },
        },
        "loggers": {
            "containercluster": {
                "level": None,
                "handlers": ["console"],
                "propagate": False,
            },
            "paramiko": {
                "level": None,
                "handlers": ["console"],
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": None,
        }
    }

    if level == "debug":
        config["handlers"]["console"]["level"] = logging.DEBUG
        config["handlers"]["console"]["formatter"] = "detailed"
        config["loggers"]["containercluster"]["level"] = logging.DEBUG
        config["loggers"]["paramiko"]["level"] = logging.DEBUG
        config["root"]["level"] = logging.DEBUG
    elif level == "normal":
        config["handlers"]["console"]["level"] = logging.INFO
        config["handlers"]["console"]["formatter"] = "brief"
        config["loggers"]["containercluster"]["level"] = logging.INFO
        config["loggers"]["paramiko"]["level"] = logging.WARN
        config["root"]["level"] = logging.INFO
    elif level == "quiet":
        config["handlers"]["console"]["level"] = logging.WARN
        config["handlers"]["console"]["formatter"] = "brief"
        config["loggers"]["containercluster"]["level"] = logging.WARN
        config["loggers"]["paramiko"]["level"] = logging.WARN
        config["root"]["level"] = logging.WARN
    else:
        raise ValueError("Invalid logging level '%s'" % (level,))

    logging.captureWarnings(True)
    logging.config.dictConfig(config)


if __name__ == "__main__":
    sys.exit(main())
