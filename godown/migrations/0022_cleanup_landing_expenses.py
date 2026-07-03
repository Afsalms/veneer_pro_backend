"""
Migration 0022: Delete all LandingExpense records that were migrated to
GRNExpense in migration 0021. Only deletes records where a matching
GRNExpense already exists on the same GRN with same category and amount.
"""
from django.db import migrations


def cleanup_landing_expenses(apps, schema_editor):
    LandingExpense = apps.get_model('godown', 'LandingExpense')
    GRNExpense     = apps.get_model('godown', 'GRNExpense')
    deleted = 0
    for le in LandingExpense.objects.all():
        # Only delete if a matching GRNExpense exists — safety guard
        has_match = GRNExpense.objects.filter(
            stock_in=le.stock_in,
            category=le.category,
            amount=le.amount,
        ).exists()
        if has_match:
            le.delete()
            deleted += 1
    print(f"  Deleted {deleted} legacy LandingExpense records.")


def reverse_noop(apps, schema_editor):
    # Cannot restore — data now lives in GRNExpense
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0021_migrate_landing_to_grn_expense'),
    ]

    operations = [
        migrations.RunPython(cleanup_landing_expenses, reverse_noop),
    ]
