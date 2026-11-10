from django.core.management.base import BaseCommand
from workbench.project_schemas import cleanup_expired_schemas


class Command(BaseCommand):
    help = 'Remove expired or completed per-file Oracle schemas, retrying failed cleanup.'

    def handle(self, *args, **options):
        cleanup_expired_schemas()
        self.stdout.write('Project schema cleanup processed.')
