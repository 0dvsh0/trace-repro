"""Seed the shop with a small, real catalogue.

A demo storefront with an empty catalogue page proves nothing, so this creates
the three things Oscar needs before its browse pages render anything: a partner,
a category tree, and products with stock records and images.

Idempotent -- every object is looked up before it is created -- and each
product is committed in its own transaction so one bad product cannot poison
the rest.
"""

from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from oscar.core.loading import get_model

Partner = get_model("partner", "Partner")
Category = get_model("catalogue", "Category")
ProductClass = get_model("catalogue", "ProductClass")
Product = get_model("catalogue", "Product")
ProductImage = get_model("catalogue", "ProductImage")
StockRecord = get_model("partner", "StockRecord")
SourceType = get_model("payment", "SourceType")

CATALOGUE = [
    {
        "title": "Aurex Studio ANC Headphones",
        "slug": "aurex-studio-anc-headphones",
        "upc": "8901234500017",
        "price": Decimal("4500.00"),
        "stock": 25,
        "colour": (36, 42, 66),
        "blurb": "Hybrid active noise cancelling, 40 hour battery, memory-foam cups.",
    },
    {
        "title": "Vaayu Pulse Earbuds",
        "slug": "vaayu-pulse-earbuds",
        "upc": "8901234500024",
        "price": Decimal("2499.00"),
        "stock": 60,
        "colour": (58, 36, 78),
        "blurb": "Low-latency earbuds with transparency mode and wireless charging.",
    },
    {
        "title": "Kestrel Bookshelf Speakers",
        "slug": "kestrel-bookshelf-speakers",
        "upc": "8901234500031",
        "price": Decimal("12900.00"),
        "stock": 8,
        "colour": (26, 58, 48),
        "blurb": "Two-way bookshelf pair, silk dome tweeter, walnut veneer.",
    },
]


def placeholder_image(colour) -> ContentFile:
    """A product picture, generated at seed time rather than committed."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 800), colour)
    draw = ImageDraw.Draw(img)
    for i in range(800):
        shade = int(46 * (i / 800))
        draw.line(
            [(0, i), (800, i)],
            fill=(min(255, colour[0] + shade), min(255, colour[1] + shade), min(255, colour[2] + shade)),
        )
    draw.ellipse([230, 230, 570, 570], outline=(255, 255, 255), width=12)
    draw.ellipse([320, 320, 480, 480], fill=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ContentFile(buf.read(), name="product.png")


class Command(BaseCommand):
    help = "Create the demo catalogue (idempotent)."

    def handle(self, *args, **options):
        partner, _ = Partner.objects.get_or_create(
            code="vaayu", defaults={"name": "Vaayu Audio"}
        )
        product_class, _ = ProductClass.objects.get_or_create(
            slug="audio", defaults={"name": "Audio"}
        )
        ProductClass.objects.filter(slug="audio").update(requires_shipping=False)

        category = Category.objects.filter(slug="headphones-earbuds").first()
        if category is None:
            root = Category.objects.add_root(name="Audio")
            category = root.add_child(name="Headphones & Earbuds", slug="headphones-earbuds")

        created = 0
        # Oscar's payment page refuses to render with no payment method
        # registered, so the shop has to actually accept cards.
        SourceType.objects.get_or_create(
            code="vaayu-gateway", defaults={"name": "Vaayu Gateway"}
        )
        for entry in CATALOGUE:
            with transaction.atomic():
                product = Product.objects.filter(slug=entry["slug"]).first()
                if product is None:
                    product = Product.objects.create(
                        title=entry["title"],
                        slug=entry["slug"],
                        upc=entry["upc"],
                        structure=Product.STANDALONE,
                        product_class=product_class,
                        description=entry["blurb"],
                        # Digital goods: Oscar skips its shipping steps, so the
                        # customer journey is basket -> payment -> order.
                        is_shipping_required=False,
                    )
                    created += 1

                # Oscar 4 keeps the partner on the stock record and the
                # category on a separate table; neither is a Product field.
                product.categories.add(category)

                # Drop image rows left behind by an interrupted seed: sorl
                # raises on a row whose file was never written, which takes the
                # whole product page down with it.
                for orphan in ProductImage.objects.filter(product=product):
                    if not orphan.original:
                        orphan.delete()

                image = (
                    ProductImage.objects.filter(product=product)
                    .order_by("display_order", "id")
                    .first()
                )
                if image is None:
                    image = ProductImage.objects.create(
                        product=product, code=f"{entry['slug']}-front", display_order=0
                    )
                if not image.original:
                    image.original.save(
                        f"{entry['slug']}.png", placeholder_image(entry["colour"]), save=True
                    )

                StockRecord.objects.update_or_create(
                    product=product,
                    partner=partner,
                    defaults={
                        # partner_sku is NOT NULL with no default, so it has to
                        # be supplied or the INSERT aborts the transaction.
                        "partner_sku": entry["upc"],
                        "price": entry["price"],
                        "price_currency": "INR",
                        "num_in_stock": entry["stock"],
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"catalogue ready: {Product.objects.count()} products "
                f"({created} new), {Category.objects.count()} categories"
            )
        )
