import os

from setuptools import find_packages, setup


def read_requirements():
    ret = []
    fname = os.path.join(os.path.dirname(__file__), "requirements.txt")
    with open(fname, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                ret.append(line)
    return ret


def read_long_description():
    with open("README.rst", "r") as f:
        return f.read()


setup(
    name="container-cluster",
    version="0.1.0",
    description="Tools to manage container clusters",
    long_description=read_long_description(),
    url="https://github.com/carletes/container-cluster",
    author="Carlos Valiente",
    author_email="carlos@pepelabs.net",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.5",
    ],

    package_dir={
        "containercluster": "containercluster",
    },
    packages=find_packages(),
    package_data={
        "containercluster": [
            "etcd-cloud-config.yaml",
            "worker-cloud-config.yaml",
        ]
    },
    entry_points={
        "console_scripts": [
            "container-cluster = containercluster.cmdline:main",
        ]
    },
    install_requires=read_requirements(),

    zip_safe=False,
)
