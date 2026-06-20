from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from datetime import date, timedelta
from decimal import Decimal
from godown.models import (Godown, UserProfile, GodownSequence,
    Customer, Supplier, Product, PurchaseOrder, PurchaseOrderItem,
    StockIn, StockInItem, LandingExpense, Sale, SaleItem, Expense, Payment)


class Command(BaseCommand):
    help = 'Seed demo data for development / testing'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding demo data...')
        today = date.today()

        # Create demo godown
        godown = Godown.objects.create(
            firm_name='AK Face Veneers', phone='+91 94470 12345',
            email='akfaceveneers@gmail.com', gstin='32AABCG1234F1Z5',
            state_code='32', bank_name='Federal Bank, Aluva',
            account_no='12345678901234', ifsc='FDRL0001234', upi_id='akfaceveneers@fbl',
            invoice_prefix='SL', po_prefix='PO', grn_prefix='GRN', gst_rate=12,
        )
        for seq_type in ('sale','po','grn','est'):
            GodownSequence.objects.create(godown=godown, seq_type=seq_type, last_num=0)

        # Admin user
        admin = User.objects.create_user('admin', password='admin123', email='admin@akveneer.com', first_name='Admin')
        UserProfile.objects.create(user=admin, godown=godown, role='admin')
        # Limited user
        staff = User.objects.create_user('staff', password='staff123', first_name='Ravi', last_name='Kumar')
        UserProfile.objects.create(user=staff, godown=godown, role='limited', phone='+91 94470 99999')

        # Suppliers
        sup1 = Supplier.objects.create(godown=godown, name='Global Veneers Ltd', phone='+91 22-4455-6677', city='Mumbai', state='Maharashtra', gst_number='27AABCG1234F1Z5', species_supplied='Teak Oak')
        sup2 = Supplier.objects.create(godown=godown, name='Southwood Veneers', phone='+91 80-2233-4455', city='Bangalore', state='Karnataka', species_supplied='Walnut Wenge')
        sup3 = Supplier.objects.create(godown=godown, name='Nature Veneers Co.', phone='+91 422-2233-445', city='Coimbatore', state='Tamil Nadu', species_supplied='Rosewood Sapele')

        # Customers
        c1 = Customer.objects.create(godown=godown, name='Sunrise Furnitures', phone='+91 94470 12345', location='Aluva, Ernakulam', state='Kerala', credit_limit=300000)
        c2 = Customer.objects.create(godown=godown, name='Kerala Wood Works', phone='+91 98450 67890', location='Thrissur', state='Kerala', credit_limit=200000)
        c3 = Customer.objects.create(godown=godown, name='Classic Interiors', phone='+91 96330 11223', location='Kochi', state='Kerala', credit_limit=150000)
        c4 = Customer.objects.create(godown=godown, name='Greenline Decor', phone='+91 99470 44556', location='Kozhikode', state='Kerala', credit_limit=100000)

        # Products
        p1 = Product.objects.create(godown=godown, species='Teak', thickness='0.6mm', cut_type='Flat Cut', finish='Natural', buy_rate=32, sale_rate=45, min_stock=500, sheet_length=8, sheet_width=4, stock_qty=0)
        p2 = Product.objects.create(godown=godown, species='Oak', thickness='0.8mm', cut_type='Rift Cut', finish='Natural', buy_rate=28, sale_rate=38, min_stock=600, sheet_length=8, sheet_width=4, stock_qty=0)
        p3 = Product.objects.create(godown=godown, species='Walnut', thickness='0.6mm', cut_type='Flat Cut', finish='Natural', buy_rate=55, sale_rate=70, min_stock=400, sheet_length=8, sheet_width=4, stock_qty=0)
        p4 = Product.objects.create(godown=godown, species='Rosewood', thickness='0.5mm', cut_type='Quarter Cut', finish='Natural', buy_rate=72, sale_rate=95, min_stock=300, sheet_length=7, sheet_width=3, stock_qty=0)
        p5 = Product.objects.create(godown=godown, species='Sapele', thickness='0.5mm', cut_type='Quarter Cut', finish='Natural', buy_rate=42, sale_rate=58, min_stock=400, sheet_length=8, sheet_width=4, stock_qty=0)

        def make_grn(supplier, items, advance=0, date_offset=0, landing=None):
            num = GodownSequence.format_number(godown, 'grn')
            grn = StockIn.objects.create(godown=godown, grn_number=num, supplier=supplier,
                date=today-timedelta(days=date_offset), amount_paid=advance, payment_mode='bank' if advance else 'credit')
            total_qty = sum(q for _,q,_ in items)
            for prod, qty, rate in items:
                prod.update_avg_cost(qty, rate)
                si = StockInItem.objects.create(stock_in=grn, product=prod,
                    qty_sqft=Decimal(str(qty)), rate_per_sqft=Decimal(str(rate)), landed_rate=Decimal(str(rate)),
                    rack_location=f'Rack {prod.species[0]}-1')
                prod.stock_qty += Decimal(str(qty)); prod.save()
            if landing:
                for cat, amt in landing:
                    LandingExpense.objects.create(stock_in=grn, category=cat, amount=Decimal(str(amt)), paid_to='Various')
                # Recalculate landed rates with landing expenses
                total_landing = sum(Decimal(str(a)) for _, a in landing)
                si_items_list = list(grn.items.all())
                total_qty = sum(si.qty_sqft for si in si_items_list)
                if total_qty > 0:
                    for si in si_items_list:
                        share = (si.qty_sqft / total_qty) * total_landing
                        si.landed_rate = si.rate_per_sqft + (share / si.qty_sqft)
                        si.save(update_fields=['landed_rate'])
                    # Update avg_cost for affected products
                    for si in si_items_list:
                        p = si.product
                        all_items = StockInItem.objects.filter(product=p)
                        rv = sum(i.qty_sqft * (i.landed_rate or i.rate_per_sqft) for i in all_items)
                        rq = sum(i.qty_sqft for i in all_items)
                        if rq > 0:
                            p.avg_cost = rv / rq
                            p.save(update_fields=['avg_cost'])
            return grn

        grn1 = make_grn(sup1, [(p1,2000,32),(p2,1000,28)], advance=35000, date_offset=6, landing=[('transport',8500),('labour',3200),('forklift',2400)])
        grn2 = make_grn(sup2, [(p3,1500,55)], advance=82500, date_offset=16)
        grn3 = make_grn(sup3, [(p4,800,72),(p5,1200,42)], date_offset=24)
        make_grn(sup1, [(p2,2000,28)], advance=84000, date_offset=32)

        def make_sale(customer, items, received=0, date_offset=0, due_offset=15):
            num = GodownSequence.format_number(godown, 'sale')
            sale = Sale.objects.create(godown=godown, bill_number=num, customer=customer,
                date=today-timedelta(days=date_offset), due_date=today+timedelta(days=due_offset),
                amount_received=Decimal(str(received)), payment_mode='bank' if received else 'credit',
                gst_rate=godown.gst_rate)
            for prod, qty, rate in items:
                cost_snap = prod.avg_cost
                SaleItem.objects.create(sale=sale, product=prod,
                    qty_sqft=Decimal(str(qty)), rate_per_sqft=Decimal(str(rate)),
                    cost_at_sale=cost_snap, grn_source=grn1)
                prod.stock_qty -= Decimal(str(qty)); prod.save()
            return sale

        s1 = make_sale(c1, [(p1,850,45)], received=38250, date_offset=0, due_offset=15)
        s2 = make_sale(c2, [(p5,800,58),(p2,200,38)], received=30000, date_offset=2, due_offset=2)
        s3 = make_sale(c3, [(p3,600,70)], received=0, date_offset=4, due_offset=4)
        s4 = make_sale(c4, [(p2,600,38)], received=24000, date_offset=7, due_offset=20)

        # Expenses
        m1 = today.replace(day=1)
        Expense.objects.create(godown=godown, category='rent', date=m1, description='Monthly rent', paid_to='Abdul Realty', amount=15000, payment_mode='bank', status='paid')
        Expense.objects.create(godown=godown, category='forklift', date=m1+timedelta(days=2), description='Forklift hire', paid_to='AK Machinery', amount=12000, payment_mode='cash', status='paid')
        Expense.objects.create(godown=godown, category='labour', date=today-timedelta(days=6), description='Loading GRN', paid_to='Daily Labour', amount=4500, payment_mode='cash', status='paid')
        Expense.objects.create(godown=godown, category='electricity', date=today-timedelta(days=1), description='KSEB Bill', paid_to='KSEB', amount=3800, payment_mode='online', status='pending')

        # POs
        po_num = GodownSequence.format_number(godown, 'po')
        po1 = PurchaseOrder.objects.create(godown=godown, po_number=po_num, supplier=sup1,
            date=today-timedelta(days=5), expected_arrival=today+timedelta(days=2),
            advance_paid=35000, advance_mode='bank')
        PurchaseOrderItem.objects.create(po=po1, product=p1, qty_sqft=2000, rate_per_sqft=32)

        # Load lookup defaults for this godown
        from godown.management.commands.load_lookup_defaults import DEFAULTS
        from godown.models import LookupValue
        for category, rows in DEFAULTS.items():
            for value, label, sort_order, is_default in rows:
                LookupValue.objects.get_or_create(
                    godown=godown, category=category, value=value,
                    defaults={'label':label,'sort_order':sort_order,'is_default':is_default,'is_active':True}
                )

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Demo data seeded!\n'
            f'  Godown: {godown.firm_name} (ID: {godown.pk})\n'
            f'  Admin login: admin / admin123\n'
            f'  Staff login: staff / staff123\n'
            f'  {Supplier.objects.filter(godown=godown).count()} suppliers, '
            f'{Customer.objects.filter(godown=godown).count()} customers, '
            f'{Product.objects.filter(godown=godown).count()} products\n'
            f'  {StockIn.objects.filter(godown=godown).count()} GRNs, '
            f'{Sale.objects.filter(godown=godown).count()} sales, '
            f'{Expense.objects.filter(godown=godown).count()} expenses'
        ))
