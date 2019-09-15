# pylint: disable=no-member

"""
:mod:`coinaddr.validation`
~~~~~~~~~~~~~~~~~~~~~~~~

Various validation machinery for validating cryptocurrency addresses.
"""

import re
from hashlib import sha256
import functools
import operator

from zope.interface import implementer, provider
import attr
import sha3
import base58check
import math
from binascii import unhexlify
import base64

from .interfaces import (
    INamedSubclassContainer, IValidator, IValidationRequest,
    IValidationResult, ICurrency
    )
from .base import NamedSubclassContainerBase
from . import currency


@provider(INamedSubclassContainer)
class Validators(metaclass=NamedSubclassContainerBase):
    """Container for all validators."""


class ValidatorMeta(type):
    """Register validator classes on Validators.validators."""

    def __new__(mcs, cls, bases, attrs):
        new = type.__new__(mcs, cls, bases, attrs)
        if new.name:
            Validators[new.name] = new
        return new


@attr.s(cmp=False, slots=True)
class ValidatorBase(metaclass=ValidatorMeta):
    """Validator Interface."""

    name = None

    request = attr.ib(
        type='ValidationRequest',
        validator=[
            lambda i, a, v: type(v).__name__ == 'ValidationRequest',
            attr.validators.provides(IValidationRequest)
            ]
    )

    def validate(self):
        """Validate the address type, return True if valid, else False."""

    @property
    def network(self):
        """Return the network derived from the network version bytes."""


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidator)
class Base58CheckValidator(ValidatorBase):
    """Validates Base58Check based cryptocurrency addresses."""

    name = 'Base58Check'
    dec_digit_to_base58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    base58_digit_to_dec = { b58:dec for dec,b58 in enumerate(dec_digit_to_base58) }


    def validate(self):
        """extended keys have their own validation"""
        if len(self.request.address) == 111:
            return self.validate_extended()

        """Validate the address."""
        if 25 > len(self.request.address) > 35:
            return False

        abytes = base58check.b58decode(
            self.request.address, **self.request.extras)

        if self.network == "":
            return False

        checksum = sha256(sha256(abytes[:-4]).digest()).digest()[:4]
        if abytes[-4:] != checksum:
            return False

        return self.request.address == base58check.b58encode(
            abytes, **self.request.extras)

    def validate_extended(self):
        if self.network == "":
            return False

        base58_stripped = self.request.address.decode('utf-8').lstrip("1")
        int_rep = 0
        for base58_digit in base58_stripped:
            int_rep *= 58
            try:
                int_rep += self.base58_digit_to_dec[base58_digit]
            except KeyError:
                return False

        hex_rep = "{:X}".format(int_rep)
        if len(hex_rep) % 2 == 1:
            hex_rep = "0" + hex_rep
        all_bytes =  base64.b16decode(hex_rep).rjust(82, b"\0")

        zero_count = next(zeros for zeros,byte in enumerate(all_bytes) if byte != "\0")
        if len(self.request.address.decode('utf-8')) - len(base58_stripped) != zero_count:
            return False

        if sha256(sha256(all_bytes[:-4]).digest()).digest()[:4] != all_bytes[-4:]:
            return False

        return True


    @property
    def network(self):
        """Return network derived from network version bytes."""
        try:
            abytes = base58check.b58decode(
                self.request.address, **self.request.extras)
        except ValueError:
            return ""

        for name, networks in self.request.currency.networks.items():
            for netw in networks:
                if netw != 0:
                    prefixlen = math.ceil(math.floor((math.log(netw) / math.log(2)) + 1) / 8)
                else:
                    prefixlen = 1
                address_prefix = [x for x in bytearray(abytes[:prefixlen])]
                if prefixtodec(address_prefix) == netw:
                    return name
        return ""

@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidator)
class EthereumValidator(ValidatorBase):
    """Validates ethereum based crytocurrency addresses."""

    name = 'Ethereum'
    non_checksummed_patterns = (
        re.compile("^(0x)?[0-9a-f]{40}$"), re.compile("^(0x)?[0-9A-F]{40}$")
        )


    def validate(self):
        """Validate the address."""
        address = self.request.address.decode()
        if any(bool(pat.match(address))
               for pat in self.non_checksummed_patterns):
            return True
        addr = address[2:] if address.startswith('0x') else address
        addr_hash = sha3.keccak_256(addr.lower().encode('ascii')).hexdigest()
        for i, letter in enumerate(addr):
            if any([
                    int(addr_hash[i], 16) >= 8 and letter.upper() != letter,
                    int(addr_hash[i], 16) < 8 and letter.lower() != letter
            ]):
                return False
        return True


    #def validate(self):
    #    """Validate the address."""
    #    address = self.request.address.decode()
    #    if any(bool(pat.match(address))
    #           for pat in self.non_checksummed_patterns):
    #        return True
    #    addr = address.lstrip('0x')
    #    addr_hash = sha3.keccak_256(addr.lower().encode('ascii')).hexdigest()
    #    for i in range(0, len(addr)):
    #        if any([
    #                int(addr_hash[i], 16) > 7 and addr[i].upper() != addr[i],
    #                int(addr_hash[i], 16) <= 7 and addr[i].lower() != addr[i]
    #        ]):
    #            return False
    #    return True

    @property
    def network(self):
        """Return network derived from network version bytes."""
        return 'both'


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidationRequest)
class ValidationRequest:
    """Contain the data and helpers as an immutable request object."""

    currency = attr.ib(
        type=currency.Currency,
        converter=currency.Currencies.get,
        validator=[
            attr.validators.instance_of(currency.Currency),
            attr.validators.provides(ICurrency)
            ])
    address = attr.ib(
        type=bytes,
        converter=lambda a: a if isinstance(a, bytes) else a.encode('ascii'),
        validator=attr.validators.instance_of(bytes))

    @property
    def extras(self):
        """Extra arguments for passing to decoder, etc."""
        extras = dict()
        if self.currency.charset:
            extras.setdefault('charset', self.currency.charset)
        return extras

    @property
    def networks(self):
        """Concatenated list of all version bytes for currency."""
        networks = tuple(self.currency.networks.values())
        return functools.reduce(operator.concat, networks)

    def execute(self):
        """Execute this request and return the result."""
        validator = Validators.get(self.currency.validator)(self)
        return ValidationResult(
            name=self.currency.name,
            ticker=self.currency.ticker,
            address=self.address,
            valid=validator.validate(),
            network=validator.network
            )


@attr.s(frozen=True, slots=True, cmp=False)
@implementer(IValidationResult)
class ValidationResult:
    """Contains an immutable representation of the validation result."""

    name = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))
    ticker = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))
    address = attr.ib(
        type=bytes,
        validator=attr.validators.instance_of(bytes))
    valid = attr.ib(
        type=bool,
        validator=attr.validators.instance_of(bool))
    network = attr.ib(
        type=str,
        validator=attr.validators.instance_of(str))

    def __bool__(self):
        return self.valid


def validate(currency, address):
    """Validate the given address according to currency type.

    This is the main entrypoint for using this library.

    :param currency str: The name or ticker code of the cryptocurrency.
    :param address (bytes, str): The crytocurrency address to validate.
    :return: a populated ValidationResult object
    :rtype: :inst:`ValidationResult`

    Usage::

      >>> import coinaddr
      >>> coinaddr.validate('btc', b'1BoatSLRHtKNngkdXEeobR76b53LETtpyT')
      ValidationResult(name='bitcoin', ticker='btc',
      ...              address=b'1BoatSLRHtKNngkdXEeobR76b53LETtpyT',
      ...              valid=True, network='main')

    """
    request = ValidationRequest(currency, address)
    return request.execute()


def prefixtodec(prefix):
    total = 0
    multiplier = 256
    for i in range(2,len(prefix)+1):
        total += prefix[-i]*multiplier
        multiplier *= 256
    return total+prefix[-1]