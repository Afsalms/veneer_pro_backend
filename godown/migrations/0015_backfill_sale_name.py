# Backfill sale_name with species for all existing products that have it blank.
# sale_name is now mandatory going forward; this migration ensures no existing
# product is left with a blank value once the NOT-blank validation is enforced
# at the application layer.

from django.db import migrations
from django.db.models import F


def backfill_sale_name(apps, schema_editor):
    Product = apps.get_model('godown', 'Product')
    Product.objects.filter(sale_name='').update(sale_name=F('species'))


def reverse_noop(apps, schema_editor):
    # No safe reverse — leave sale_name values as-is on rollback
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0014_product_sale_name'),
    ]

    operations = [
        migrations.RunPython(backfill_sale_name, reverse_noop),
    ]
