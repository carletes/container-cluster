import datetime
import os
import threading
import uuid

import ipaddress

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


__all__ = [
    "CA",
]


ONE_DAY = datetime.timedelta(1, 0, 0)


class CA(object):

    _lock = threading.RLock()

    def __init__(self, ca_dir):
        self.ca_dir = ca_dir

    @property
    def cert_path(self):
        fname, _ = self._ensure_ca_cert()
        return fname

    @property
    def key_path(self):
        _, fname = self._ensure_ca_cert()
        return fname

    def generate_cert(self, host_name, alt_names=None):
        if alt_names is None:
            alt_names = []
        cert_path = os.path.join(self.certs_dir, host_name + ".pem")
        key_path = os.path.join(self.certs_dir, host_name + "-key.pem")
        with CA._lock:
            if not (os.access(cert_path, os.F_OK) and
                    os.access(key_path, os.F_OK)):
                key = rsa.generate_private_key(public_exponent=65537,
                                               key_size=2048,
                                               backend=default_backend())
                public_key = key.public_key()
                with open(key_path, "wb") as f:
                    f.write(key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.TraditionalOpenSSL,
                        encryption_algorithm=serialization.NoEncryption()
                    ))

                _, ca_key_path = self._ensure_ca_cert()
                with open(ca_key_path, "rb") as f:
                    ca_key = serialization.load_pem_private_key(
                        data=f.read(),
                        password=None,
                        backend=default_backend()
                    )
                    ca_public_key = ca_key.public_key()
                b = self._builder()
                b = b.public_key(public_key)
                b = b.subject_name(x509.Name([
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Container cluster"),
                    x509.NameAttribute(NameOID.COMMON_NAME, unicode(host_name))
                ]))
                b = b.add_extension(
                    x509.BasicConstraints(ca=False,
                                          path_length=None),
                    critical=True
                )
                b = b.add_extension(
                    x509.KeyUsage(digital_signature=True,
                                  content_commitment=False,
                                  key_encipherment=True,
                                  data_encipherment=False,
                                  key_agreement=False,
                                  key_cert_sign=False,
                                  crl_sign=False,
                                  encipher_only=False,
                                  decipher_only=False),
                    critical=True
                )
                b = b.add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,
                                           ExtendedKeyUsageOID.CLIENT_AUTH]),
                    critical=False
                )
                b = b.add_extension(
                    x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_public_key),
                    critical=False
                )
                b = b.add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(public_key),
                    critical=False
                )
                if alt_names:
                    b = b.add_extension(
                        x509.SubjectAlternativeName([
                            x509_name(name) for name in alt_names
                        ]),
                        critical=False
                    )
                cert = b.sign(private_key=ca_key,
                              algorithm=hashes.SHA256(),
                              backend=default_backend())
                with open(cert_path, "wb") as f:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))

        return cert_path, key_path

    def _ensure_ca_cert(self):
        cert_path = os.path.join(self.ca_dir, "ca.pem")
        key_path = os.path.join(self.ca_dir, "ca-key.pem")
        with CA._lock:
            if not (os.access(cert_path, os.F_OK) and
                    os.access(key_path, os.F_OK)):
                key = rsa.generate_private_key(public_exponent=65537,
                                               key_size=2048,
                                               backend=default_backend())
                public_key = key.public_key()
                with open(key_path, "wb") as f:
                    f.write(key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.TraditionalOpenSSL,
                        encryption_algorithm=serialization.NoEncryption()
                    ))

                b = self._builder()
                b = b.subject_name(self.issuer)
                b = b.public_key(public_key)
                b = b.add_extension(x509.BasicConstraints(ca=True,
                                                          path_length=2),
                                    critical=True)
                b = b.add_extension(x509.KeyUsage(digital_signature=False,
                                                  content_commitment=False,
                                                  key_encipherment=False,
                                                  data_encipherment=False,
                                                  key_agreement=False,
                                                  key_cert_sign=True,
                                                  crl_sign=True,
                                                  encipher_only=False,
                                                  decipher_only=False),
                                    critical=True)
                b = b.add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(public_key),
                    critical=False
                )
                b = b.add_extension(
                    x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
                    critical=False
                )
                cert = b.sign(private_key=key,
                              algorithm=hashes.SHA256(),
                              backend=default_backend())
                with open(cert_path, "wb") as f:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))

        return cert_path, key_path

    def _builder(self):
        b = x509.CertificateBuilder()
        b = b.issuer_name(self.issuer)
        b = b.not_valid_before(datetime.datetime.today() - ONE_DAY)
        b = b.not_valid_after(datetime.datetime(2026, 1, 1))
        b = b.serial_number(int(uuid.uuid4()))
        return b

    @property
    def issuer(self):
        return x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Container cluster"),
            x509.NameAttribute(NameOID.COMMON_NAME, u"Container cluster CA"),
        ])

    @property
    def certs_dir(self):
        dname = os.path.join(self.ca_dir, "certs")
        with CA._lock:
            if not os.access(dname, os.F_OK):
                os.mkdir(dname)
        return dname


def x509_name(name):
    try:
        addr = ipaddress.ip_address(name)
        return x509.IPAddress(addr)
    except ValueError:
        return x509.DNSName(name)
