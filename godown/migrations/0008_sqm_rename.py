"""
Migration 0008: Rename qty_sqft→qty_sqm and rate_per_sqft→rate_per_sqm across all models.
Add sheet_sqm and sheet_sqm_override to Product.
All quantities now stored in sq.m directly — no conversion needed.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0007_sale_gsp_fields'),
    ]

    operations = [
        # ── Product: add sheet_sqm fields ────────────────────────
        migrations.AddField('product', 'sheet_sqm',
            models.DecimalField(max_digits=8, decimal_places=4, default=0,
                help_text='Auto-calculated sq.m per sheet (length × width × 0.0929)')),
        migrations.AddField('product', 'sheet_sqm_override',
            models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                help_text='Override sq.m per sheet (supplier-adjusted). Used if set.')),

        # ── PurchaseOrderItem ─────────────────────────────────────
        migrations.RenameField('purchaseorderitem', 'qty_sqft',  'qty_sqm'),
        migrations.RenameField('purchaseorderitem', 'rate_per_sqft', 'rate_per_sqm'),
        migrations.AlterField('purchaseorderitem', 'qty_sqm',
            models.DecimalField(max_digits=10, decimal_places=4,
                help_text='Quantity in sq.m — canonical unit')),

        # ── StockInItem ───────────────────────────────────────────
        migrations.RenameField('stockinitem', 'qty_sqft',  'qty_sqm'),
        migrations.RenameField('stockinitem', 'rate_per_sqft', 'rate_per_sqm'),
        migrations.AlterField('stockinitem', 'qty_sqm',
            models.DecimalField(max_digits=10, decimal_places=4,
                help_text='Quantity in sq.m')),

        # ── SaleItem ──────────────────────────────────────────────
        migrations.RenameField('saleitem', 'qty_sqft',  'qty_sqm'),
        migrations.RenameField('saleitem', 'rate_per_sqft', 'rate_per_sqm'),
        migrations.AlterField('saleitem', 'qty_sqm',
            models.DecimalField(max_digits=10, decimal_places=4)),

        # ── StockDamage ───────────────────────────────────────────
        migrations.RenameField('stockdamage', 'qty_sqft', 'qty_sqm'),
        migrations.AlterField('stockdamage', 'qty_sqm',
            models.DecimalField(max_digits=10, decimal_places=4)),

        # ── EstimationItem ────────────────────────────────────────
        migrations.RenameField('estimationitem', 'qty_sqft',  'qty_sqm'),
        migrations.RenameField('estimationitem', 'rate_per_sqft', 'rate_per_sqm'),
        migrations.AlterField('estimationitem', 'qty_sqm',
            models.DecimalField(max_digits=10, decimal_places=4)),

        # ── Product: stock_qty precision ──────────────────────────
        migrations.AlterField('product', 'stock_qty',
            models.DecimalField(max_digits=12, decimal_places=4, default=0,
                help_text='Current stock in sq.m')),
        migrations.AlterField('product', 'min_stock',
            models.DecimalField(max_digits=10, decimal_places=4, default=0,
                help_text='Minimum stock level in sq.m for reorder alerts')),
    ]
