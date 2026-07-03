"""
Migration 0021: Move all existing LandingExpense records into GRNExpense.
- Each LandingExpense becomes one GRNExpense with expense_number GE-1, GE-2 ...
- amount_paid carried over
- vendor carried over
- LandingExpense records are KEPT for historical references but effectively
  superseded by GRNExpense going forward
"""
from django.db import migrations
from django.utils import timezone


def migrate_landing_to_grn_expense(apps, schema_editor):
    LandingExpense = apps.get_model('godown', 'LandingExpense')
    GRNExpense     = apps.get_model('godown', 'GRNExpense')
    GodownSequence = apps.get_model('godown', 'GodownSequence')

    # Group by godown to generate GE numbers per godown
    godown_counters = {}

    for le in LandingExpense.objects.select_related('stock_in__godown', 'vendor').order_by('id'):
        godown = le.stock_in.godown
        gid = godown.pk
        if gid not in godown_counters:
            godown_counters[gid] = 0
        godown_counters[gid] += 1
        num = godown_counters[gid]
        expense_number = f"GE-{num}"

        GRNExpense.objects.create(
            godown=godown,
            expense_number=expense_number,
            stock_in=le.stock_in,
            date=le.stock_in.date,
            category=le.category,
            description=le.description,
            amount=le.amount,
            vendor=le.vendor,
            amount_paid=le.amount_paid,
        )
        # Update the sequence counter so new GE numbers don't collide
        seq, _ = GodownSequence.objects.get_or_create(
            godown=godown, seq_type='grn_expense',
            defaults={'last_num': 0}
        )
        if seq.last_num < num:
            seq.last_num = num
            seq.save(update_fields=['last_num'])


def reverse_noop(apps, schema_editor):
    GRNExpense = apps.get_model('godown', 'GRNExpense')
    GRNExpense.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0020_grn_expense_model'),
    ]

    operations = [
        migrations.RunPython(migrate_landing_to_grn_expense, reverse_noop),
    ]
