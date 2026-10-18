"""Rebuild sorl-thumbnail's cache.

sorl keeps its key/value store in the database but the rendered files on disk.
Moving MEDIA_ROOT, restoring a dump, or seeding before the store existed leaves
entries whose files were never written, and the template tag then emits URLs
that 404. Clearing the store forces a lazy rebuild.
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Clear sorl-thumbnail's key/value store so images regenerate."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM thumbnail_kvstore")
            cleared = cursor.rowcount
        self.stdout.write(self.style.SUCCESS(f"thumbnail cache cleared ({cleared} rows)"))

        from oscar.core.loading import get_model
        from sorl.thumbnail import get_thumbnail

        Product = get_model("catalogue", "Product")
        made = 0
        for product in Product.objects.all():
            image = product.primary_image()
            if image is not None:
                get_thumbnail(image.original, "300x300")
                made += 1
        self.stdout.write(self.style.SUCCESS(f"regenerated {made} thumbnails"))