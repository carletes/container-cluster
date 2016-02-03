import logging
import tempfile

from pytest import fixture, yield_fixture

from containercluster import core, mockprovider, providers
from containercluster.config import Config


providers.PROVIDERS[mockprovider.MockProvider.name] = (
    mockprovider.__name__, "MockProvider"
)
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(threadName)s %(name)s %(message)s")


@fixture
def config(scope="function"):
    home = tempfile.mkdtemp(prefix="container-cluster-test-")
    return Config(home)


@yield_fixture
def mock_cluster(scope="function"):
    home = tempfile.mkdtemp(prefix="container-cluster-test-")
    conf = Config(home)
    provider = providers.get_provider("mockprovider")
    cluster = core.create_cluster("test-cluster1", "alpha", 3, "512mb", 4,
                                  "1gb", provider, "lon1", conf)
    with cluster.provider.ssh_server:
        yield cluster
