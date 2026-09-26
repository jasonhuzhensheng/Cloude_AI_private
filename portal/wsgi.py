import os, ipaddress, re
from urllib.parse import urlsplit
os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings')
from django.core.wsgi import get_wsgi_application
_django_application=get_wsgi_application()

def application(environ,start_response):
    # Runpod's HTTPS proxy rewrites Host to an internal IP and omits the original
    # host header. Canonicalize only internal IP hosts to this Pod's fixed domain.
    pod=os.environ.get('RUNPOD_POD_ID','')
    if re.fullmatch(r'[a-z0-9]+',pod):
        try:
            address=ipaddress.ip_address(urlsplit('//'+environ.get('HTTP_HOST','')).hostname)
            if address.is_private or address in ipaddress.ip_network('100.64.0.0/10'):
                port='8888' if os.environ.get('PORTAL_SETUP')=='1' else '8090'
                environ['HTTP_HOST']=f'{pod}-{port}.proxy.runpod.net'
        except ValueError:
            pass
    return _django_application(environ,start_response)
