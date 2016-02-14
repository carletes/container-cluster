import os

import pytest

from containercluster import digitalocean


token_env_var = "DIGITALOCEAN_ACCESS_TOKEN"
pytestmark = pytest.mark.skipif(token_env_var not in os.environ,
                                reason="Undefined env variable '%s'" %
                                (token_env_var,))


@pytest.fixture
def provider():
    return digitalocean.DigitalOceanProvider()


def test_valid_instance_size(provider):
    assert provider.get_size("512mb")


def test_invalid_instance_size(provider):
    with pytest.raises(ValueError) as exc:
        provider.get_size("unknown-size")
    assert str(exc.value).startswith("Unsupported size")


def test_valid_image(provider):
    assert provider.get_image("alpha")


def test_invalid_image(provider):
    with pytest.raises(Exception) as exc:
        provider.get_image("unknown-channel")
    assert str(exc.value) == ("Cannot find CoreOS image for channel "
                              "'unknown-channel'")


def test_valid_location(provider):
    assert provider.get_location("lon1")


def test_invalid_location(provider):
    with pytest.raises(ValueError) as exc:
        provider.get_location("unknown-location")
    assert str(exc.value).startswith("Unsupported location")
