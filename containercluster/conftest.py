import logging
import tempfile

from pytest import fixture

from containercluster import mockprovider
from containercluster.config import Config


# Keep pyflakes happy
_ = mockprovider

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(threadName)s %(name)s %(message)s")


@fixture
def config(scope="function"):
    home = tempfile.mkdtemp(prefix="container-cluster-test-")
    return Config(home)
