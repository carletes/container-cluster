from containercluster import core


def test_cloud_config_data(config):
    config.add_cluster("test-cluster1", "alpha", 3, None, 0, None, None, None)

    cluster = core.Cluster("test-cluster1", None, config)
    assert cluster.cloud_config_data("etcd").startswith("#cloud-config")
    assert cluster.cloud_config_data("worker") is None
    assert cluster.cloud_config_data("unknown-node-type") is None


def test_create_cluster(config):
    cluster_name = "test-cluster1"
    assert cluster_name not in config.clusters

    cluster = core.create_cluster("test-cluster1", "alpha", 3, "512mb", 4,
                                  "1gb", "mockprovider", "lon1", config)
    assert len(cluster.nodes) == 7
    for node in cluster.nodes:
        assert node.name.startswith("test-cluster1-")

    assert cluster_name in config.clusters
