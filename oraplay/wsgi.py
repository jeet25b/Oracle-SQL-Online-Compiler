import os
from django.core.wsgi import get_wsgi_application
from .oracle_service import configure_oracle_service
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")
application = configure_oracle_service(get_wsgi_application())
