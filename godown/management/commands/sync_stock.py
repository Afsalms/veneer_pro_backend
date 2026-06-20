"""
Management command: sync_stock
Recalculates stock_qty for every product from first principles.
Use if stock ever gets out of sync (shouldn't happen in normal use).

Usage:
    python manage.py sync_stock
    python manage.py sync_stock --godown 1   # specific godown only
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.db.models.functions import Coalesce
from decimal import Decimal
from godown.models import Godown, Product, StockInItem, SaleItem, StockDamage


class Command(BaseCommand):
    help = 'Recalculate product stock quantities from GRNs, sales and damage records'

    def add_arguments(self, parser):
        parser.add_argument('--godown', type=int, help='Godown ID (default: all godowns)')
        parser.add_argument('--dry-run', action='store_true', help='Show what would change without saving')

    def handle(self, *args, **options):
        godown_id = options.get('godown')
        dry_run   = options.get('dry_run')

        godowns = Godown.objects.filter(pk=godown_id) if godown_id else Godown.objects.all()

        total_fixed = 0
        for godown in godowns:
            self.stdout.write(f'\n{godown.firm_name} (ID:{godown.pk})')
            products = Product.objects.filter(godown=godown)
            for prod in products:
                received = StockInItem.objects.filter(
                    product=prod, stock_in__godown=godown
                ).aggregate(t=Coalesce(Sum('qty_sqft'), Decimal('0')))['t']

                sold = SaleItem.objects.filter(
                    product=prod, sale__godown=godown
                ).aggregate(t=Coalesce(Sum('qty_sqft'), Decimal('0')))['t']

                damaged = StockDamage.objects.filter(
                    product=prod, godown=godown
                ).aggregate(t=Coalesce(Sum('qty_sqft'), Decimal('0')))['t']

                correct = received - sold - damaged

                if abs(correct - prod.stock_qty) > Decimal('0.01'):
                    self.stdout.write(
                        f'  {"[DRY RUN] " if dry_run else ""}FIXED {prod.display_name}: '
                        f'{prod.stock_qty:.2f} → {correct:.2f} sqft '
                        f'(received={received:.0f}, sold={sold:.0f}, damaged={damaged:.0f})'
                    )
                    if not dry_run:
                        prod.stock_qty = correct
                        prod.save(update_fields=['stock_qty'])
                    total_fixed += 1
                else:
                    self.stdout.write(f'  ✓ {prod.display_name}: {prod.stock_qty:.2f} sqft — OK')

        self.stdout.write(
            self.style.SUCCESS(f'\nDone. {total_fixed} product(s) {"would be " if dry_run else ""}corrected.')
        )
