"""Serve explicitly public assets before Django's authenticated middleware."""
from whitenoise import WhiteNoise
from django.conf import settings


def with_static_files(application):
    from workbench.portal_views import PORTAL_ASSETS, PROJECT_DIR
    from workbench.views import _STATIC_FILES, ROOT

    static = WhiteNoise(application, max_age=300)
    for name in PORTAL_ASSETS:
        path = PROJECT_DIR / name
        if path.is_file():
            static.add_file_to_dictionary('/portal/assets/' + name, str(path))
    for name in _STATIC_FILES:
        path = ROOT / name
        if path.suffix.lower() in {'.js', '.css'} and path.is_file():
            static.add_file_to_dictionary('/' + name, str(path))
    if settings.STATIC_ROOT.is_dir():
        static.add_files(str(settings.STATIC_ROOT), prefix=settings.STATIC_URL)
    return static
