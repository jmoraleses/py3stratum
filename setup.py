#!/usr/bin/env python
from setuptools import setup
from stratum import version

setup(name='stratum',
      version=version.VERSION,
      description='Stratum server implementation based on Twisted',
      author='slush',
      author_email='info@bitcion.cz',
      url='http://blog.bitcoin.cz/stratum',
      packages=['stratum',],
      py_modules=['distribute_setup',],
      zip_safe=False,
      install_requires=['setuptools-rust', 'twisted', 'ecdsa', 'cryptography>=35.0', 'pyopenssl', 'autobahn', 'pyasn1', 'service-identity',]
     )
