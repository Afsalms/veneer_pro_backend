"""
Migration 0009: Sync model changes not captured in previous migrations.
- Product.stock_qty: decimal_places 2→4
- Product.min_stock: decimal_places 2→4
- Product.sheet_sqm_override: ensure nullable
- Product.uom: add blank=True
- StockInItem.qty_unit: default sqft→sqm
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0008_sqm_rename'),
    ]

    operations = [
        # stock_qty: 4dp precision for sq.m
        migrations.AlterField('product', 'stock_qty',
            models.DecimalField(max_digits=12, decimal_places=4, default=0,
                help_text='Current stock in sq.m')),

        # min_stock: 4dp precision for sq.m
        migrations.AlterField('product', 'min_stock',
            models.DecimalField(max_digits=10, decimal_places=4, default=0,
                help_text='Minimum stock level in sq.m')),

        # sheet_sqm_override: ensure nullable in DB
        migrations.AlterField('product', 'sheet_sqm_override',
            models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                help_text='Override sq.m per sheet — used if set instead of auto value')),

        # uom: blank=True so it can be empty
        migrations.AlterField('product', 'uom',
            models.CharField(max_length=5, blank=True, default='SQF',
                help_text='Unit of measure for GSP/e-invoice')),

        # qty_unit default: sqft→sqm
        migrations.AlterField('stockinitem', 'qty_unit',
            models.CharField(max_length=5,
                choices=[('sqm', 'Square Metres'), ('pcs', 'Pieces')],
                default='sqm')),
    ]
