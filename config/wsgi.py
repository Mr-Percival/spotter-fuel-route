"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


# Build the in-memory place and station indexes when the server starts, so the
# first request doesn't pay for it (management commands never import this file).
try:
    from routing.services.places import _index as _places_index
    from routing.services.stations import get_station_index

    _places_index()
    get_station_index()
except Exception as exc:  # e.g. stations not loaded yet: build lazily later
    import logging

    logging.getLogger(__name__).warning("Index warm-up skipped: %s", exc)
