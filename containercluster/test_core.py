from containercluster import core


def mock_cluster(config):
    return core.create_cluster("test-cluster1", "alpha", 3, "512mb", 4,
                               "1gb", "mockprovider", "lon1", config)


def test_cloud_config_template(config):
    for node in mock_cluster(config).nodes:
        assert node.cloud_config_template.startswith("#cloud-config")


def test_create_cluster(config):
    cluster_name = "test-cluster1"
    assert cluster_name not in config.clusters

    cluster = mock_cluster(config)
    assert len(cluster.nodes) == 7
    for node in cluster.nodes:
        assert node.name.startswith("test-cluster1-")

    assert cluster_name in config.clusters
