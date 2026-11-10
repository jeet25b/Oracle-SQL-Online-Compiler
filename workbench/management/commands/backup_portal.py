"""Create or validate an encrypted portal backup without touching Oracle sessions."""
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command


def backup_cipher():
    key = hashlib.sha256((settings.SECRET_KEY + ":portal-backup:v1").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def validate_backup(path):
    try:
        plaintext = backup_cipher().decrypt(Path(path).read_bytes())
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            for name in archive.namelist():
                target = PurePosixPath(name)
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError("Invalid archive path")
            records = json.loads(archive.read("portal.json"))
            if not isinstance(records, list) or any(not isinstance(row, dict) or "model" not in row for row in records):
                raise ValueError("Invalid data fixture")
            bad = archive.testzip()
            if bad: raise ValueError("Archive checksum failed")
            return {"records": len(records), "media_files": sum(name.startswith("media/") for name in archive.namelist())}
    except (InvalidToken, ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        raise CommandError("Backup is unreadable, damaged, or belongs to a different application secret key.") from error


class Command(BaseCommand):
    help = "Create an encrypted portal data/media backup, or verify a backup without restoring it."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--output", help="New backup file. Existing files are never overwritten.")
        group.add_argument("--verify", help="Check decryption, fixture format, archive paths and checksums.")
        parser.add_argument("--without-media", action="store_true", help="Back up database records only.")

    def handle(self, *args, **options):
        if options["verify"]:
            result = validate_backup(options["verify"])
            self.stdout.write(self.style.SUCCESS(f"Backup verified: {result['records']} records; {result['media_files']} media files. No data restored."))
            return
        destination = Path(options["output"]).expanduser().resolve()
        if destination.exists(): raise CommandError("Choose a new backup filename. Existing backups are not overwritten.")
        fixture = io.StringIO()
        call_command("dumpdata", "workbench", "auth", "contenttypes", natural_foreign=True,
                     natural_primary=True, stdout=fixture, verbosity=0)
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("portal.json", fixture.getvalue())
            if not options["without_media"]:
                root = Path(settings.MEDIA_ROOT).resolve()
                if root.exists():
                    for source in root.rglob("*"):
                        if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(root):
                            archive.write(source, "media/" + source.relative_to(root).as_posix())
        encrypted = backup_cipher().encrypt(content.getvalue())
        try:
            with destination.open("xb") as output: output.write(encrypted)
        except OSError as error:
            raise CommandError("Could not write the new backup file.") from error
        result = validate_backup(destination)
        self.stdout.write(self.style.SUCCESS(f"Encrypted backup created and verified: {result['records']} records; {result['media_files']} media files."))
