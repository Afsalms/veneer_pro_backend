"""
Management command: load_lookup_defaults
Populates LookupValue table with default values for all existing godowns.
Safe to run multiple times — uses get_or_create.

Usage:
    python manage.py load_lookup_defaults              # all godowns
    python manage.py load_lookup_defaults --godown 1   # specific godown ID
"""
from django.core.management.base import BaseCommand
from godown.models import Godown, LookupValue, LookupCategory


# ── Default lookup data ──────────────────────────────────────────
DEFAULTS = {
    LookupCategory.THICKNESS: [
        # (value, label, sort_order, is_default)
        ('0.3mm', '0.3mm',  10, False),
        ('0.5mm', '0.5mm',  20, False),
        ('0.6mm', '0.6mm',  30, True),
        ('0.8mm', '0.8mm',  40, False),
        ('1.0mm', '1.0mm',  50, False),
        ('1.2mm', '1.2mm',  60, False),
        ('1.5mm', '1.5mm',  70, False),
        ('2.0mm', '2.0mm',  80, False),
    ],
    LookupCategory.CUT_TYPE: [
        ('Flat Cut',    'Flat Cut',    10, True),
        ('Quarter Cut', 'Quarter Cut', 20, False),
        ('Rift Cut',    'Rift Cut',    30, False),
        ('Rotary Cut',  'Rotary Cut',  40, False),
        ('Crown Cut',   'Crown Cut',   50, False),
    ],
    LookupCategory.FINISH: [
        ('Natural',          'Natural',          10, True),
        ('Dyed',             'Dyed',             20, False),
        ('Backed (Paper)',   'Backed (Paper)',   30, False),
        ('Backed (Fabric)',  'Backed (Fabric)',  40, False),
        ('Bleached',         'Bleached',         50, False),
        ('Pre-polished',     'Pre-polished',     60, False),
    ],
    LookupCategory.EXPENSE_CATEGORY: [
        ('rent',        'Godown Rent',         10, False),
        ('forklift',    'Forklift Rent',       20, False),
        ('labour',      'Labour / Loading',    30, False),
        ('transport',   'Transport / Freight', 40, False),
        ('electricity', 'Electricity',         50, False),
        ('salary',      'Staff Salary',        60, False),
        ('maintenance', 'Maintenance',         70, False),
        ('misc',        'Miscellaneous',       80, False),
    ],
    LookupCategory.LANDING_EXPENSE_CATEGORY: [
        ('transport', 'Transportation',    10, False),
        ('labour',    'Labour / Loading',  20, False),
        ('forklift',  'Forklift Charges',  30, False),
        ('customs',   'Customs / Duty',    40, False),
        ('insurance', 'Insurance',         50, False),
        ('misc',      'Miscellaneous',     60, False),
    ],
}


class Command(BaseCommand):
    help = 'Load default lookup values for all godowns (safe to re-run)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--godown', type=int, default=None,
            help='Load for a specific godown ID only'
        )

    def handle(self, *args, **options):
        godown_id = options.get('godown')
        if godown_id:
            godowns = Godown.objects.filter(pk=godown_id)
            if not godowns.exists():
                self.stderr.write(f'Godown ID {godown_id} not found.')
                return
        else:
            godowns = Godown.objects.all()

        total_created = 0
        total_existing = 0

        for godown in godowns:
            self.stdout.write(f'\n  Godown: {godown.firm_name} (ID:{godown.pk})')
            for category, rows in DEFAULTS.items():
                created_count = 0
                for value, label, sort_order, is_default in rows:
                    obj, created = LookupValue.objects.get_or_create(
                        godown=godown,
                        category=category,
                        value=value,
                        defaults={
                            'label': label,
                            'sort_order': sort_order,
                            'is_default': is_default,
                            'is_active': True,
                        }
                    )
                    if created:
                        created_count += 1
                        total_created += 1
                    else:
                        total_existing += 1
                self.stdout.write(
                    f'    {category.label}: {created_count} created'
                )

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Done! {total_created} values created, '
            f'{total_existing} already existed.'
        ))
