from decimal import Decimal
from django.db import models
from django.db import transaction
from django.contrib.auth.models import User
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────
# GODOWN (Company / Tenant)
# Each client company is one Godown. All data is scoped to a Godown.
# ─────────────────────────────────────────────────────────────────

class Godown(models.Model):
    """One row per client company."""
    firm_name      = models.CharField(max_length=200)
    address        = models.TextField(blank=True)
    phone          = models.CharField(max_length=30, blank=True)
    email          = models.CharField(max_length=100, blank=True)
    gstin          = models.CharField(max_length=20, blank=True)
    state_code     = models.CharField(max_length=2, default='32', help_text='Kerala=32')
    bank_name      = models.CharField(max_length=100, blank=True)
    account_no     = models.CharField(max_length=30, blank=True)
    ifsc           = models.CharField(max_length=15, blank=True)
    upi_id         = models.CharField(max_length=100, blank=True)
    gst_rate       = models.DecimalField(max_digits=5, decimal_places=2, default=12)

    # GSP / e-Invoice integration fields
    gsp_username   = models.CharField(max_length=100, blank=True,
                         help_text='GSP API username (e.g. Masters India / IRIS)')
    gsp_client_id  = models.CharField(max_length=100, blank=True,
                         help_text='GSP Client ID for API auth')
    gsp_client_secret = models.CharField(max_length=200, blank=True,
                         help_text='GSP Client Secret (stored encrypted in production)')
    gsp_sandbox    = models.BooleanField(default=True,
                         help_text='Use sandbox/test API endpoint')
    # Firm details needed for e-invoice
    pan_number     = models.CharField(max_length=10, blank=True)
    cin_number     = models.CharField(max_length=21, blank=True,
                         help_text='Company Identification Number (if applicable)')
    invoice_prefix = models.CharField(max_length=10, default='SL')
    po_prefix      = models.CharField(max_length=10, default='PO')
    grn_prefix     = models.CharField(max_length=10, default='GRN')
    invoice_note   = models.TextField(blank=True,
                         default='Goods once sold will not be taken back.')
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.firm_name

    @property
    def cgst_rate(self):
        return self.gst_rate / 2

    @property
    def sgst_rate(self):
        return self.gst_rate / 2


class GodownSequence(models.Model):
    """Per-godown auto-increment sequences for document numbers."""
    SEQ_CHOICES = [('sale','Sale'),('po','Purchase Order'),('grn','GRN'),('est','Estimation')]
    godown   = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='sequences')
    seq_type = models.CharField(max_length=10, choices=SEQ_CHOICES)
    last_num = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('godown', 'seq_type')

    @classmethod
    def next(cls, godown, seq_type):
        """Atomically increment and return the next number."""
        with transaction.atomic():
            obj, _ = cls.objects.select_for_update().get_or_create(
                godown=godown, seq_type=seq_type,
                defaults={'last_num': 0}
            )
            obj.last_num += 1
            obj.save(update_fields=['last_num'])
            return obj.last_num

    @classmethod
    def format_number(cls, godown, seq_type):
        """Returns formatted doc number e.g. SL-1001, PO-301, GRN-201."""
        n = cls.next(godown, seq_type)
        prefix_map = {
            'sale':      godown.invoice_prefix,
            'po':        godown.po_prefix,
            'grn':       godown.grn_prefix,
            'est':       'EST',
            'cash_memo': 'CM',
        }
        # Starting offsets per doc type for readability
        offset_map = {'sale': 1000, 'po': 300, 'grn': 200, 'est': 100}
        num = n + offset_map.get(seq_type, 0)
        prefix = prefix_map.get(seq_type, seq_type.upper())
        return f"{prefix}-{num}"


# ─────────────────────────────────────────────────────────────────
# USER PROFILE
# ─────────────────────────────────────────────────────────────────

class UserProfile(models.Model):
    ROLE_CHOICES = [('admin', 'Admin'), ('limited', 'Limited User')]
    user    = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    godown  = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='users')
    role    = models.CharField(max_length=10, choices=ROLE_CHOICES, default='limited')
    phone   = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} @ {self.godown.firm_name} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == 'admin'


# ─────────────────────────────────────────────────────────────────
# CORE BUSINESS MODELS — all scoped to a Godown
# ─────────────────────────────────────────────────────────────────

class Supplier(models.Model):
    godown          = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='suppliers')
    name            = models.CharField(max_length=200)
    phone           = models.CharField(max_length=20, blank=True)
    city            = models.CharField(max_length=100, blank=True)
    state           = models.CharField(max_length=100, blank=True)
    gst_number      = models.CharField(max_length=20, blank=True)
    address         = models.TextField(blank=True)
    species_supplied= models.CharField(max_length=300, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def total_payable(self):
        return sum(g.balance for g in self.purchase_orders.all())


class Customer(models.Model):
    godown       = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='customers')
    name         = models.CharField(max_length=200)
    phone        = models.CharField(max_length=20, blank=True)
    location     = models.CharField(max_length=200, blank=True)
    gst_number   = models.CharField(max_length=20, blank=True)
    state        = models.CharField(max_length=50, default='Kerala')
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # GSP / e-Invoice buyer details
    gstin        = models.CharField(max_length=15, blank=True,
                       help_text='Buyer GSTIN — required for e-invoice')
    pincode      = models.CharField(max_length=6, blank=True,
                       help_text='Buyer pincode — required for e-Way Bill')
    state_code   = models.CharField(max_length=2, blank=True,
                       help_text='2-digit state code e.g. 32 for Kerala')
    place_of_supply = models.CharField(max_length=2, blank=True,
                       help_text='Place of supply state code for GST')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def total_outstanding(self):
        return sum(s.balance for s in self.sales.prefetch_related('items').all())

    @property
    def initials(self):
        parts = self.name.split()
        return (parts[0][0] + parts[1][0]).upper() if len(parts) >= 2 else self.name[:2].upper()

    @property
    def status(self):
        if self.total_outstanding <= 0:
            return 'active'
        today = timezone.now().date()
        overdue = any(
            s.balance > 0 and s.due_date and s.due_date < today
            for s in self.sales.prefetch_related('items').all()
        )
        return 'overdue' if overdue else 'dues_pending'


class Product(models.Model):
    THICKNESS_CHOICES = [('0.5mm','0.5mm'),('0.6mm','0.6mm'),
                         ('0.8mm','0.8mm'),('1.0mm','1.0mm'),('1.2mm','1.2mm')]
    CUT_CHOICES = [('Flat Cut','Flat Cut'),('Quarter Cut','Quarter Cut'),
                   ('Rift Cut','Rift Cut'),('Rotary Cut','Rotary Cut')]
    FINISH_CHOICES = [('Natural','Natural'),('Dyed','Dyed'),
                      ('Backed (Paper)','Backed (Paper)'),('Backed (Fabric)','Backed (Fabric)')]

    godown      = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='products')
    species     = models.CharField(max_length=100)
    thickness   = models.CharField(max_length=10, choices=THICKNESS_CHOICES, default='0.6mm')
    cut_type    = models.CharField(max_length=20, choices=CUT_CHOICES, default='Flat Cut')
    finish      = models.CharField(max_length=20, choices=FINISH_CHOICES, default='Natural')
    buy_rate    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sale_rate   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    min_stock   = models.DecimalField(max_digits=10, decimal_places=4, default=500,
                      help_text='Minimum stock level in sq.m')
    hsn_code    = models.CharField(max_length=8, blank=True, default='4408',
                      help_text='HSN code for e-invoice (face veneer = 4408)')
    uom         = models.CharField(max_length=5, blank=True, default='SQF',
                      help_text='Unit of measure for GSP/e-invoice (SQF = square feet, internal unit)')
    stock_qty   = models.DecimalField(max_digits=12, decimal_places=4, default=0,
                      help_text='Current stock in sq.m')
    sheet_length        = models.DecimalField(max_digits=6, decimal_places=2, default=8)
    sheet_width         = models.DecimalField(max_digits=6, decimal_places=2, default=4)
    sheet_sqm           = models.DecimalField(max_digits=8, decimal_places=4, default=0,
                              help_text='Auto-calculated sq.m per sheet (length × width × 0.0929)')
    sheet_sqm_override  = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                              help_text='Override sq.m per sheet — used if set instead of auto value')
    avg_cost    = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['species', 'thickness']

    def __str__(self):
        return f"{self.species} {self.thickness} {self.cut_type}"

    @property
    def stock_status(self):
        if self.stock_qty <= 0: return 'out'
        if self.stock_qty < self.min_stock * Decimal('0.25'): return 'critical'
        if self.stock_qty < self.min_stock: return 'low'
        return 'ok'

    @property
    def display_name(self):
        return f"{self.species} {self.thickness}"

    @property
    def sheet_sqft(self):
        return self.sheet_length * self.sheet_width

    @property
    def effective_sheet_sqm(self):
        """Returns override if set, else auto-calculated sheet_sqm."""
        if self.sheet_sqm_override is not None:
            return self.sheet_sqm_override
        return self.sheet_sqm

    @property
    def stock_value(self):
        return self.stock_qty * self.avg_cost

    def update_avg_cost(self, incoming_qty, incoming_rate):
        existing_value = self.stock_qty * self.avg_cost
        incoming_value = Decimal(str(incoming_qty)) * Decimal(str(incoming_rate))
        new_qty = self.stock_qty + Decimal(str(incoming_qty))
        if new_qty > 0:
            self.avg_cost = (existing_value + incoming_value) / new_qty
        self.save(update_fields=['avg_cost'])


class PurchaseOrder(models.Model):
    STATUS_CHOICES = [('pending','Pending'),('partial','Partially Received'),
                      ('received','Fully Received'),('cancelled','Cancelled')]
    PAYMENT_MODE_CHOICES = [('credit','Credit'),('cash','Cash'),
                            ('bank','Bank Transfer'),('cheque','Cheque'),('upi','UPI')]

    CURRENCY_CHOICES = [('INR','Indian Rupee (₹)'),('USD','US Dollar ($)')]

    godown          = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='purchase_orders')
    po_number       = models.CharField(max_length=30)
    supplier        = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='pos')
    date            = models.DateField(default=timezone.now)
    expected_arrival= models.DateField(null=True, blank=True)
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    # Currency — suppliers may invoice in USD
    currency        = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='INR')
    # Advance payment — stored in original currency + exchange rate at time of payment
    advance_paid    = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                          help_text='Amount in original currency (INR or USD)')
    advance_exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1,
                                help_text='Exchange rate at time of advance (USD→INR). 1 if INR.')
    advance_mode    = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES, default='bank', blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('godown', 'po_number')
        ordering = ['-date']

    def __str__(self):
        return self.po_number

    @property
    def total_value(self):
        """Always in INR."""
        return sum(i.amount for i in self.po_items.all())

    @property
    def advance_paid_inr(self):
        """Advance converted to INR using locked exchange rate."""
        if self.currency == 'USD':
            return self.advance_paid * self.advance_exchange_rate
        return self.advance_paid

    @property
    def balance_on_arrival(self):
        return self.total_value - self.advance_paid_inr

    @property
    def days_to_arrival(self):
        if not self.expected_arrival:
            return None
        return (self.expected_arrival - timezone.now().date()).days

    @property
    def is_overdue(self):
        return bool(self.expected_arrival and
                    self.days_to_arrival is not None and
                    self.days_to_arrival < 0 and
                    self.status == 'pending')

    def update_status_from_grns(self):
        """
        Recalculate PO status based on actual GRN quantities received.

        Logic:
          - If no items ordered: skip
          - If all items fully received (qty_received >= qty_sqft): → 'received'
          - If some items partially received (at least one > 0):    → 'partial'
          - If nothing received yet:                                → 'pending'
          - Cancelled status is never changed here.
        """
        if self.status == 'cancelled':
            return  # never auto-change a cancelled PO

        items = list(self.po_items.all())
        if not items:
            return

        total_ordered  = sum(i.qty_sqm for i in items)
        total_received = sum(i.qty_received for i in items)

        if total_received <= 0:
            new_status = 'pending'
        elif total_received >= total_ordered:
            new_status = 'received'
        else:
            new_status = 'partial'

        if self.status != new_status:
            self.status = new_status
            self.save(update_fields=['status'])


class PurchaseOrderItem(models.Model):
    UNIT_CHOICES = [('sqft','Square Feet'),('pcs','Pieces')]
    po           = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='po_items')
    product      = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty_sqm      = models.DecimalField(max_digits=10, decimal_places=4,
                       help_text='Quantity in sq.m — canonical unit')
    rate_per_sqm = models.DecimalField(max_digits=10, decimal_places=2)
    # Original unit tracking
    qty_unit     = models.CharField(max_length=5, choices=UNIT_CHOICES, default='sqft')
    pieces       = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sheet_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sheet_width  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    @property
    def amount(self):
        return self.qty_sqm * self.rate_per_sqm

    @property
    def qty_received(self):
        """Total sqft actually received across all GRNs linked to this PO for this product."""
        from django.db.models import Sum
        result = StockInItem.objects.filter(
            stock_in__po=self.po,
            product=self.product,
        ).aggregate(total=Sum('qty_sqm'))['total']
        return result or Decimal('0')

    @property
    def qty_pending(self):
        return max(Decimal('0'), self.qty_sqm - self.qty_received)

    @property
    def is_fully_received(self):
        return self.qty_received >= self.qty_sqm

    @property
    def qty_display(self):
        if self.qty_unit == 'pcs' and self.pieces:
            return f"{self.pieces:.0f} pcs × {self.sheet_length}×{self.sheet_width}ft = {self.qty_sqm:.4f} sq.m"
        return f"{self.qty_sqm:.4f} sq.m"


class StockIn(models.Model):
    PAYMENT_MODE_CHOICES = [('credit','Credit'),('cash','Cash'),
                            ('bank','Bank Transfer'),('cheque','Cheque'),('upi','UPI')]

    godown         = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='stock_ins')
    grn_number     = models.CharField(max_length=30)
    supplier       = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchase_orders')
    po             = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL,
                         null=True, blank=True, related_name='grns')
    date           = models.DateField(default=timezone.now)
    invoice_number = models.CharField(max_length=50, blank=True)
    vehicle_number = models.CharField(max_length=20, blank=True)
    # Payment at GRN time — may differ from PO currency/rate
    amount_paid    = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                         help_text='Amount in payment currency')
    payment_currency = models.CharField(max_length=3, default='INR',
                           choices=[('INR','Indian Rupee (₹)'),('USD','US Dollar ($)')])
    exchange_rate  = models.DecimalField(max_digits=10, decimal_places=4, default=1,
                         help_text='Exchange rate at time of this payment (USD→INR). 1 if INR.')
    payment_mode   = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES, default='credit')
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('godown', 'grn_number')
        ordering = ['-date']

    def __str__(self):
        return self.grn_number

    @property
    def items_total(self):
        return sum(i.amount for i in self.items.all())

    @property
    def landing_expenses_total(self):
        return sum(e.amount for e in self.landing_expenses.all())

    @property
    def total_amount(self):
        return self.items_total + self.landing_expenses_total

    @property
    def amount_paid_inr(self):
        """Payment converted to INR using locked exchange rate."""
        if self.payment_currency == 'USD':
            return self.amount_paid * self.exchange_rate
        return self.amount_paid

    @property
    def balance(self):
        """Balance always in INR."""
        advance_inr = self.po.advance_paid_inr if self.po else Decimal('0')
        return self.total_amount - self.amount_paid_inr - advance_inr


class StockInItem(models.Model):
    UNIT_CHOICES = [('sqft','Square Feet'),('pcs','Pieces')]
    stock_in      = models.ForeignKey(StockIn, on_delete=models.CASCADE, related_name='items')
    product       = models.ForeignKey(Product, on_delete=models.PROTECT)
    rack_location = models.CharField(max_length=20, blank=True)
    qty_sqm       = models.DecimalField(max_digits=10, decimal_places=4,
                        help_text='Quantity in sq.m')
    rate_per_sqm  = models.DecimalField(max_digits=10, decimal_places=2)
    landed_rate   = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    # Original unit
    qty_unit      = models.CharField(max_length=5, choices=UNIT_CHOICES, default='sqm')
    pieces        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sheet_length  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sheet_width   = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    @property
    def amount(self):
        return self.qty_sqm * self.rate_per_sqm

    @property
    def landed_amount(self):
        return self.qty_sqm * self.landed_rate

    @property
    def qty_display(self):
        if self.qty_unit == 'pcs' and self.pieces:
            return f"{self.pieces:.0f} pcs × {self.sheet_length}×{self.sheet_width}ft = {self.qty_sqm:.4f} sq.m"
        return f"{self.qty_sqm:.4f} sq.m"

    @property
    def qty_sold(self):
        from django.db.models import Sum
        result = SaleItem.objects.filter(
            grn_source=self.stock_in, product=self.product
        ).aggregate(total=Sum('qty_sqm'))['total']
        return result or Decimal('0')

    @property
    def qty_remaining(self):
        return self.qty_sqm - self.qty_sold

    @property
    def revenue_from_batch(self):
        from django.db.models import Sum, F
        result = SaleItem.objects.filter(
            grn_source=self.stock_in, product=self.product
        ).aggregate(total=Sum(F('qty_sqm') * F('rate_per_sqm')))['total']
        return result or Decimal('0')

    @property
    def profit_from_batch(self):
        return self.revenue_from_batch - (self.qty_sold * self.landed_rate)


class LandingExpense(models.Model):
    CATEGORY_CHOICES = [
        ('transport','Transportation'),('labour','Labour / Loading'),
        ('forklift','Forklift Charges'),('customs','Customs / Duty'),
        ('insurance','Insurance'),('misc','Miscellaneous'),
    ]
    stock_in    = models.ForeignKey(StockIn, on_delete=models.CASCADE, related_name='landing_expenses')
    category    = models.CharField(max_length=15, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=200, blank=True)
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    paid_to     = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.get_category_display()} – ₹{self.amount}"


def get_fifo_grn(product, qty_needed, godown):
    result = []
    remaining = Decimal(str(qty_needed))
    grn_items = StockInItem.objects.filter(
        product=product, stock_in__godown=godown
    ).select_related('stock_in').order_by('stock_in__date', 'stock_in__id')
    for item in grn_items:
        if remaining <= 0:
            break
        available = item.qty_remaining
        if available <= 0:
            continue
        take = min(available, remaining)
        result.append((item, take))
        remaining -= take
    return result, remaining


# ── Stock Damage ──────────────────────────────────────────────────
class StockDamage(models.Model):
    DAMAGE_CATEGORY = [
        ('transit',  'Transit Damage'),
        ('quality',  'Quality Reject'),
        ('moisture', 'Moisture / Warping'),
        ('handling', 'Handling Damage'),
        ('fire',     'Fire / Flood'),
        ('other',    'Other'),
    ]

    godown      = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='damages')
    grn         = models.ForeignKey(StockIn, on_delete=models.SET_NULL,
                      related_name='damages', null=True, blank=True)
    product     = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='damages')
    date        = models.DateField(default=timezone.now)
    category    = models.CharField(max_length=20, choices=DAMAGE_CATEGORY, default='other')
    qty_sqm     = models.DecimalField(max_digits=10, decimal_places=4)
    cost_rate   = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                      help_text='Cost per sqft at time of damage (auto-filled from product avg cost)')
    description = models.TextField(blank=True)
    reported_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                      null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.product} — {self.qty_sqm:.4f} sq.m ({self.get_category_display()})"

    @property
    def write_off_value(self):
        return (self.qty_sqm * self.cost_rate).quantize(Decimal('0.01'))


class Sale(models.Model):
    PAYMENT_MODE_CHOICES = [('credit','Credit'),('cash','Cash'),
                            ('bank','Bank Transfer'),('cheque','Cheque'),('upi','UPI')]
    SALE_TYPE_CHOICES    = [('bill','Tax Invoice'),('cash_memo','Cash Memo (No Bill)')]

    godown          = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='sales')
    # bill = formal GST invoice; cash_memo = no bill, separate numbering
    sale_type       = models.CharField(max_length=10, choices=SALE_TYPE_CHOICES, default='bill')
    bill_number     = models.CharField(max_length=30)
    customer        = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='sales')
    date            = models.DateField(default=timezone.now)
    due_date        = models.DateField(null=True, blank=True)
    transport       = models.CharField(max_length=100, blank=True)
    po_reference    = models.CharField(max_length=50, blank=True)
    delivery_address= models.TextField(blank=True)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_mode    = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES, default='credit')
    is_igst         = models.BooleanField(default=False)
    gst_rate        = models.DecimalField(max_digits=5, decimal_places=2, default=12)
    created_at      = models.DateTimeField(auto_now_add=True)

    # ── GSP / e-Invoice / e-Way Bill readiness ────────────────────
    # IRN — assigned by IRP after e-invoice submission
    irn              = models.CharField(max_length=64, blank=True)
    irn_date         = models.DateTimeField(null=True, blank=True)
    ack_number       = models.CharField(max_length=20, blank=True)
    qr_code_data     = models.TextField(blank=True)
    # e-Way Bill
    eway_bill_number = models.CharField(max_length=12, blank=True)
    eway_bill_date   = models.DateTimeField(null=True, blank=True)
    eway_bill_valid_upto = models.DateTimeField(null=True, blank=True)
    # Transporter (for e-Way Bill)
    transporter_id   = models.CharField(max_length=15, blank=True)
    transporter_name = models.CharField(max_length=100, blank=True)
    vehicle_number   = models.CharField(max_length=10, blank=True)
    transport_mode   = models.CharField(max_length=1, blank=True,
                           choices=[('1','Road'),('2','Rail'),('3','Air'),('4','Ship')], default='1')
    transport_distance = models.PositiveIntegerField(null=True, blank=True,
                           help_text='Distance in KM')
    # Dispatch address (if different from godown)
    dispatch_name    = models.CharField(max_length=100, blank=True)
    dispatch_addr1   = models.CharField(max_length=100, blank=True)
    dispatch_pincode = models.CharField(max_length=6, blank=True)
    dispatch_state   = models.CharField(max_length=2, blank=True)
    # Ship-to (buyer delivery address)
    ship_name        = models.CharField(max_length=100, blank=True)
    ship_addr1       = models.CharField(max_length=100, blank=True)
    ship_pincode     = models.CharField(max_length=6, blank=True)
    ship_state       = models.CharField(max_length=2, blank=True)
    notes            = models.TextField(blank=True)

    class Meta:
        unique_together = ('godown', 'bill_number')
        ordering = ['-date']

    def __str__(self):
        return self.bill_number

    @property
    def total_amount(self):
        return sum(i.amount for i in self.items.all())

    @property
    def balance(self):
        """Outstanding balance = grand_total (taxable + GST) minus what's been received."""
        return self.grand_total - self.amount_received

    @property
    def status(self):
        bal = self.balance
        if bal <= Decimal('0.01'): return 'paid'
        if self.amount_received > 0: return 'partial'
        return 'unpaid'

    @property
    def is_overdue(self):
        return bool(self.due_date and self.balance > Decimal('0.01') and
                    self.due_date < timezone.now().date())

    @property
    def taxable_amount(self):
        return self.total_amount

    @property
    def gst_amount(self):
        return (self.taxable_amount * self.gst_rate / 100).quantize(Decimal('0.01'))

    @property
    def grand_total(self):
        return self.taxable_amount + self.gst_amount

    @property
    def cgst(self):
        return Decimal('0') if self.is_igst else \
               (self.taxable_amount * self.gst_rate / 2 / 100).quantize(Decimal('0.01'))

    @property
    def sgst(self):
        return Decimal('0') if self.is_igst else \
               (self.taxable_amount * self.gst_rate / 2 / 100).quantize(Decimal('0.01'))

    @property
    def igst(self):
        return self.gst_amount if self.is_igst else Decimal('0')

    @property
    def amount_in_words(self):
        n = int(self.grand_total)
        ones = ['','One','Two','Three','Four','Five','Six','Seven','Eight','Nine',
                'Ten','Eleven','Twelve','Thirteen','Fourteen','Fifteen','Sixteen',
                'Seventeen','Eighteen','Nineteen']
        tens = ['','','Twenty','Thirty','Forty','Fifty','Sixty','Seventy','Eighty','Ninety']
        def _w(n):
            if n == 0: return ''
            elif n < 20: return ones[n]
            elif n < 100: return tens[n//10]+(' '+ones[n%10] if n%10 else '')
            elif n < 1000: return ones[n//100]+' Hundred'+(' '+_w(n%100) if n%100 else '')
            elif n < 100000: return _w(n//1000)+' Thousand'+(' '+_w(n%1000) if n%1000 else '')
            elif n < 10000000: return _w(n//100000)+' Lakh'+(' '+_w(n%100000) if n%100000 else '')
            else: return _w(n//10000000)+' Crore'+(' '+_w(n%10000000) if n%10000000 else '')
        return (_w(n) or 'Zero')+' Rupees Only'


class SaleItem(models.Model):
    sale         = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product      = models.ForeignKey(Product, on_delete=models.PROTECT)
    size         = models.CharField(max_length=30, blank=True)
    qty_sqm      = models.DecimalField(max_digits=10, decimal_places=4)
    rate_per_sqm = models.DecimalField(max_digits=10, decimal_places=2)
    cost_at_sale = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    grn_source   = models.ForeignKey(StockIn, on_delete=models.SET_NULL,
                       null=True, blank=True, related_name='sale_items')

    @property
    def amount(self):
        return self.qty_sqm * self.rate_per_sqm

    @property
    def cost_amount(self):
        return self.qty_sqm * self.cost_at_sale

    @property
    def gross_profit(self):
        return self.amount - self.cost_amount

    @property
    def margin_pct(self):
        if self.amount == 0: return Decimal('0')
        return (self.gross_profit / self.amount * 100).quantize(Decimal('0.1'))


class Expense(models.Model):
    CATEGORY_CHOICES = [('rent','Godown Rent'),('forklift','Forklift Rent'),
                        ('labour','Labour / Loading'),('transport','Transport / Freight'),
                        ('electricity','Electricity'),('misc','Miscellaneous')]
    PAYMENT_MODE_CHOICES = [('cash','Cash'),('bank','Bank Transfer'),
                            ('upi','UPI'),('cheque','Cheque'),('online','Online')]
    STATUS_CHOICES = [('paid','Paid'),('pending','Pending')]

    godown       = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='expenses')
    category     = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    date         = models.DateField(default=timezone.now)
    description  = models.CharField(max_length=300)
    paid_to      = models.CharField(max_length=200)
    amount       = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES, default='cash')
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='paid')
    bill_number  = models.CharField(max_length=50, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_category_display()} - {self.date}"


class Payment(models.Model):
    PAYMENT_MODE_CHOICES = [('cash','Cash'),('bank','Bank Transfer / NEFT'),
                            ('upi','UPI'),('cheque','Cheque'),('rtgs','RTGS'),('neft','NEFT')]
    sale        = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    date        = models.DateField(default=timezone.now)
    amount      = models.DecimalField(max_digits=12, decimal_places=2)
    mode        = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES)
    reference   = models.CharField(max_length=100, blank=True)
    note        = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"₹{self.amount} on {self.date} for {self.sale.bill_number}"


class StockAlert(models.Model):
    product         = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='alert')
    reorder_point   = models.DecimalField(max_digits=10, decimal_places=2)
    reorder_qty     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    lead_days       = models.PositiveSmallIntegerField(default=14)
    avg_daily_sales = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active       = models.BooleanField(default=True)

    @property
    def days_of_stock(self):
        if self.avg_daily_sales and self.avg_daily_sales > 0:
            return round(float(self.product.stock_qty / self.avg_daily_sales), 1)
        return None

    @property
    def needs_reorder(self):
        return self.is_active and self.product.stock_qty <= self.reorder_point


class Estimation(models.Model):
    """Customer quotation / estimate — before a formal sale."""
    STATUS_CHOICES = [('draft','Draft'),('sent','Sent'),('accepted','Accepted'),('expired','Expired')]
    godown       = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='estimations')
    est_number   = models.CharField(max_length=30)
    customer     = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='estimations', null=True, blank=True)
    customer_name= models.CharField(max_length=200, blank=True, help_text='Free text if no customer record')
    customer_phone=models.CharField(max_length=20, blank=True)
    date         = models.DateField(default=timezone.now)
    valid_until  = models.DateField(null=True, blank=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    notes        = models.TextField(blank=True)
    gst_rate     = models.DecimalField(max_digits=5, decimal_places=2, default=12)
    include_gst  = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('godown', 'est_number')
        ordering = ['-date']

    def __str__(self):
        return self.est_number

    @property
    def subtotal(self):
        return sum(i.amount for i in self.est_items.all())

    @property
    def gst_amount(self):
        if not self.include_gst: return Decimal('0')
        return (self.subtotal * self.gst_rate / 100).quantize(Decimal('0.01'))

    @property
    def total(self):
        return self.subtotal + self.gst_amount

    @property
    def display_name(self):
        if self.customer:
            return self.customer.name
        return self.customer_name or 'Walk-in Customer'

    @property
    def days_valid(self):
        if not self.valid_until: return None
        return (self.valid_until - timezone.now().date()).days


class EstimationItem(models.Model):
    estimation   = models.ForeignKey(Estimation, on_delete=models.CASCADE, related_name='est_items')
    product      = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True)
    description  = models.CharField(max_length=200, blank=True)
    qty_sqm      = models.DecimalField(max_digits=10, decimal_places=4)
    rate_per_sqm = models.DecimalField(max_digits=10, decimal_places=2)
    pieces       = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sheet_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sheet_width  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    @property
    def amount(self):
        return self.qty_sqm * self.rate_per_sqm

    @property
    def display_desc(self):
        if self.product:
            return str(self.product)
        return self.description


# ─────────────────────────────────────────────────────────────────
# LOOKUP TABLES — editable by admin, per-godown
# ─────────────────────────────────────────────────────────────────

class LookupCategory(models.TextChoices):
    THICKNESS              = 'thickness',              'Product Thickness'
    CUT_TYPE               = 'cut_type',               'Cut Type'
    FINISH                 = 'finish',                 'Finish'
    EXPENSE_CATEGORY       = 'expense_category',       'Expense Category'
    LANDING_EXPENSE_CATEGORY = 'landing_expense_category', 'Landing Expense Category'


class LookupValue(models.Model):
    """
    A single row in a lookup table (e.g. Thickness='0.6mm', CutType='Flat Cut').
    Scoped per godown so each company can have their own set of values.
    """
    godown      = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='lookup_values')
    category    = models.CharField(max_length=30, choices=LookupCategory.choices)
    value       = models.CharField(max_length=100, help_text='The stored value (e.g. 0.6mm)')
    label       = models.CharField(max_length=100, help_text='Display label (e.g. 0.6mm Thin)')
    sort_order  = models.PositiveSmallIntegerField(default=0)
    is_active   = models.BooleanField(default=True)
    is_default  = models.BooleanField(default=False,
                      help_text='Pre-selected in forms')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('godown', 'category', 'value')
        ordering = ['category', 'sort_order', 'label']

    def __str__(self):
        return f"{self.category}: {self.label}"

    @classmethod
    def for_godown(cls, godown, category):
        """Return active values for a category, ordered."""
        return cls.objects.filter(
            godown=godown, category=category, is_active=True
        ).order_by('sort_order', 'label')

    @classmethod
    def choices_for(cls, godown, category):
        """Return as (value, label) tuples for use in forms/templates."""
        return [(v.value, v.label)
                for v in cls.for_godown(godown, category)]

    @classmethod
    def default_for(cls, godown, category):
        """Return the default value string, or first active value."""
        qs = cls.for_godown(godown, category)
        default = qs.filter(is_default=True).first()
        if default:
            return default.value
        first = qs.first()
        return first.value if first else ''


# ── Bank Accounts & Statement ──────────────────────────────────────
class BankAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ('savings',  'Savings Account'),
        ('current',  'Current Account'),
        ('cc',       'Cash Credit'),
        ('od',       'Overdraft'),
        ('wallet',   'Digital Wallet / UPI'),
    ]
    godown       = models.ForeignKey(Godown, on_delete=models.CASCADE, related_name='bank_accounts')
    account_name = models.CharField(max_length=100, help_text='e.g. SBI Current A/c, HDFC Savings')
    bank_name    = models.CharField(max_length=100, blank=True)
    account_no   = models.CharField(max_length=30, blank=True)
    ifsc         = models.CharField(max_length=15, blank=True)
    upi_id       = models.CharField(max_length=100, blank=True)
    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPE_CHOICES, default='current')
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                          help_text='Opening balance when account was added to system')
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['account_name']

    def __str__(self):
        return f"{self.account_name} ({self.bank_name})" if self.bank_name else self.account_name

    @property
    def current_balance(self):
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        from django.db.models import DecimalField
        credits = BankTransaction.objects.filter(
            account=self, txn_type='credit'
        ).aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
        debits  = BankTransaction.objects.filter(
            account=self, txn_type='debit'
        ).aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
        return self.opening_balance + credits - debits


class BankTransaction(models.Model):
    TXN_TYPE_CHOICES = [('credit', 'Credit (Money In)'), ('debit', 'Debit (Money Out)')]
    TXN_CATEGORY_CHOICES = [
        ('sale_receipt',    'Sale Receipt'),
        ('supplier_payment','Supplier Payment'),
        ('expense',         'Expense'),
        ('advance_paid',    'Advance Paid'),
        ('advance_received','Advance Received'),
        ('transfer_in',     'Transfer In'),
        ('transfer_out',    'Transfer Out'),
        ('opening',         'Opening Balance'),
        ('other',           'Other'),
    ]
    account     = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name='transactions')
    date        = models.DateField()
    txn_type    = models.CharField(max_length=10, choices=TXN_TYPE_CHOICES)
    category    = models.CharField(max_length=20, choices=TXN_CATEGORY_CHOICES, default='other')
    amount      = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=300, blank=True)
    reference   = models.CharField(max_length=100, blank=True,
                      help_text='UTR / cheque number / reference')
    # Links to source documents
    sale        = models.ForeignKey('Sale', on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='bank_txns')
    grn         = models.ForeignKey('StockIn', on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='bank_txns')
    expense     = models.ForeignKey('Expense', on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='bank_txns')
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        sign = '+' if self.txn_type == 'credit' else '-'
        return f"{sign}₹{self.amount} on {self.date} — {self.description}"
