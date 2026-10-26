from ipaddress import IPv4Address, AddressValueError

from django.conf import settings
from django.http.request import split_domain_port


class PrivateNetworkHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "10.7.*.*" in settings.ALLOWED_HOSTS:
            raw_host = request._get_raw_host()
            domain, port = split_domain_port(raw_host)
            try:
                address = IPv4Address(domain)
            except AddressValueError:
                address = None
            if address is not None and address.packed[:2] == bytes((10, 7)):
                request.get_host = lambda: raw_host
        return self.get_response(request)
