import tempfile

import ipaddress

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from pytest import fixture

from containercluster.ca import CA


@fixture(scope="function")
def ca():
    dname = tempfile.mkdtemp()
    return CA(dname)


def test_ca_files(ca):
    with open(ca.cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    assert cert.issuer == ca.issuer
    assert cert.subject == ca.issuer

    with open(ca.key_path, "rb") as f:
        assert serialization.load_pem_private_key(f.read(),
                                                  password=None,
                                                  backend=default_backend())


def test_generate_cert(ca):
    cert_path, key_path = ca.generate_cert(u"example.com",
                                           [u"www.example.com", u"1.2.3.4"])
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    assert cert.issuer == ca.issuer
    assert cert.subject != ca.issuer

    with open(key_path, "rb") as f:
        assert serialization.load_pem_private_key(f.read(),
                                                  password=None,
                                                  backend=default_backend())


def test_cert_alt_names(ca):
    cert_path, _ = ca.generate_cert(u"example.com",
                                    [u"www.example.com", u"1.2.3.4"])
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read(), default_backend())

    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ext.get_values_for_type(x509.DNSName) == [u"www.example.com"]
    assert ext.get_values_for_type(x509.IPAddress) == [ipaddress.IPv4Address(u"1.2.3.4")]
