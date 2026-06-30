# Backfill existing LandingExpense.amount_paid values into a single
# VendorPayment record each, so historical payment totals aren't lost
# now that payments are tracked individually.

from django.db import migrations


def backfill_payments(apps, schema_editor):
    LandingExpense = apps.get_model('godown', 'LandingExpense')
    VendorPayment  = apps.get_model('godown', 'VendorPayment')
    for exp in LandingExpense.objects.filter(amount_paid__gt=0):
        VendorPayment.objects.create(
            expense=exp,
            date=exp.stock_in.date,
            amount=exp.amount_paid,
            payment_mode='cash',
            reference='(migrated — original total payment)',
        )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0018_vendor_payment_log'),
    ]

    operations = [
        migrations.RunPython(backfill_payments, reverse_noop),
    ]
