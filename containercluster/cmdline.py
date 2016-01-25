import argparse
import logging
import sys


from containercluster import config, core

from containercluster import providers

# Keep pyflakes happy

_ = providers


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
                          default=core.default_provider().default_etcd_size)
    create_p.add_argument("--num-workers", metavar="NUM", type=int,
                          help="number of worker nodes (default: %(default)s)",
                          default=2)
    create_p.add_argument("--size-workers", metavar="SIZE",
                          help="size for worker nodes (default: %(default)s)",
                          default=core.default_provider().default_worker_size)
    create_p.add_argument("--location", metavar="LOCATION",
                          help="instance location (default: %(default)s)",
                          default=core.default_provider().default_location)
    create_p.add_argument("--provider", metavar="PROVIDER",
                          help="cloud provider (default: %(default)s)",
                          choices=core.provider_names(),
                          default=core.default_provider().name)
    create_p.set_defaults(func=create_cluster)

    destroy_p = subp.add_parser("destroy", description=destroy_cluster.__doc__)
    destroy_p.add_argument("name", metavar="NAME", help="cluster name")
    destroy_p.set_defaults(func=destroy_cluster)

    up_p = subp.add_parser("up", description=cluster_up.__doc__)
    up_p.add_argument("name", metavar="NAME", help="cluster name")
    up_p.set_defaults(func=cluster_up)

    down_p = subp.add_parser("down", description=cluster_down.__doc__)
    down_p.add_argument("name", metavar="NAME", help="cluster name")
    down_p.set_defaults(func=cluster_down)

    args = p.parse_args()

    if args.quiet:
        log_level = logging.WARN
        log_format = "%(message)s"
    elif args.debug:
        log_level = logging.DEBUG
        log_format = "%(asctime)s %(threadName)s %(name)s %(message)s"
    else:
        log_level = logging.INFO
        log_format = "%(message)s"
    logging.basicConfig(level=log_level, stream=sys.stderr, format=log_format)

    try:
        return args.func(args)
    except Exception as exc:
        LOG.exception("Command `%s` failed: %s",
                      " ".join(sys.argv), exc.message)
        return 1


LOG = logging.getLogger("containercluster")


def create_cluster(args):
    """Create a cluster and start all its nodes.

    """
    conf = config.Config()
    if args.name in conf.clusters:
        LOG.error("Cluster `%s` already exists", args.name)
        return 1

    core.create_cluster(args.name, args.channel, args.num_etcd, args.size_etcd,
                        args.num_workers, args.size_workers, args.provider,
                        args.location, conf)
    return cluster_up(args)


def destroy_cluster(args):
    """Destroy a cluster.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        LOG.error("Unknown cluster '%s'", args.name)
        return 1
    return core.destroy_cluster(args.name, conf)


def cluster_up(args):
    """Ensure all nodes of an existing cluster are up.

    """
    conf = config.Config()
    if args.name not in conf.clusters:
        LOG.error("Unknown cluster '%s'", args.name)
        return 1
    return core.start_cluster(args.name, conf)


def cluster_down(args):
    """Ensure all nodes of an existing cluster are down.

    """


if __name__ == "__main__":
    sys.exit(main())
