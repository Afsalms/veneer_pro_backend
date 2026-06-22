"""
Migration 0010: Fix decimal precision for stock_qty and min_stock on SQLite.
SQLite AlterField doesn't change column type — use RunSQL to recreate columns.
On PostgreSQL this runs correctly via ALTER COLUMN.
"""
from django.db import migrations, models
import django.db.migrations.operations.special


class Migration(migrations.Migration):

    dependencies = [
        ('godown', '0009_model_cleanups'),
    ]

    operations = [
        # On SQLite: recreate table with new column types
        # On PostgreSQL: AlterField handles this natively
        migrations.SeparateDatabaseAndState(
            database_operations=[
                # SQLite: rebuild the table (Django does this automatically for SQLite)
                migrations.AlterField('product', 'stock_qty',
                    models.DecimalField(max_digits=12, decimal_places=4, default=0)),
                migrations.AlterField('product', 'min_stock',
                    models.DecimalField(max_digits=10, decimal_places=4, default=0)),
                migrations.AlterField('stockinitem', 'qty_unit',
                    models.CharField(max_length=5,
                        choices=[('sqm','Square Metres'),('pcs','Pieces')],
                        default='sqm')),
            ],
            state_operations=[
                migrations.AlterField('product', 'stock_qty',
                    models.DecimalField(max_digits=12, decimal_places=4, default=0,
                        help_text='Current stock in sq.m')),
                migrations.AlterField('product', 'min_stock',
                    models.DecimalField(max_digits=10, decimal_places=4, default=0,
                        help_text='Minimum stock level in sq.m')),
                migrations.AlterField('stockinitem', 'qty_unit',
                    models.CharField(max_length=5,
                        choices=[('sqm','Square Metres'),('pcs','Pieces')],
                        default='sqm')),
            ],
        ),
    ]
