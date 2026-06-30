from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.db import models as db_models
from django.db.models import F as DbF
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from functools import wraps
from decimal import Decimal
from datetime import timedelta, date

MAX_USERS_PER_GODOWN = 5   # maximum users allowed per godown

# ── Preview helper ────────────────────────────────────────────────
def _preview_response(request, title, icon, header, items, item_cols,
                      totals, confirm_url, warning=''):
    """
    Render a preview page with all POST data embedded as hidden fields.
    The confirm form POSTs to confirm_url with confirmed=1.
    """
    # Preserve all POST data (exclude csrfmiddlewaretoken)
    form_data = {}
    for key in request.POST:
        if key == 'csrfmiddlewaretoken': continue
        form_data[key] = request.POST.getlist(key)
    return render(request, 'godown/preview.html', {
        'preview_title':    title,
        'preview_icon':     icon,
        'preview_header':   header,
        'preview_items':    items,
        'preview_item_cols':item_cols,
        'preview_totals':   totals,
        'preview_warning':  warning,
        'confirm_url':      confirm_url,
        'form_data':        form_data,
    })



from .models import (
    Godown, GodownSequence, UserProfile,
    Customer, Supplier, Product,
    PurchaseOrder, PurchaseOrderItem,
    StockIn, StockInItem, LandingExpense,
    Sale, SaleItem, Expense, Payment, StockAlert,
    Estimation, EstimationItem,
    LookupValue, LookupCategory,
    StockDamage, BankAccount, BankTransaction, VendorPayment,
    get_fifo_grn,
)



# ── Pagination helper ─────────────────────────────────────────────
PAGE_SIZE = 25

# ── Date range helper ─────────────────────────────────────────────
def get_date_range(request):
    """
    Parse ?range=  param and return (date_from, date_to, label, prev_from, prev_to).
    Ranges:
      this_month   — 1st of this month → today
      last_month   — full previous calendar month
      last_3m      — last 3 complete months + current
      last_6m      — last 6 complete months + current
      this_year    — Jan 1 of this year → today
      lifetime     — earliest possible → today
      custom       — uses ?from=YYYY-MM-DD&to=YYYY-MM-DD
    Default: this_month
    """
    from datetime import datetime
    today   = timezone.now().date()
    rng     = request.GET.get('range', 'this_month')

    first_of_month = today.replace(day=1)
    first_of_year  = today.replace(month=1, day=1)

    if rng == 'this_month':
        date_from = first_of_month
        date_to   = today
        label     = f"This Month ({today.strftime('%b %Y')})"
        # Previous period: same month last year
        prev_from = first_of_month.replace(year=first_of_month.year-1)
        prev_to   = today.replace(year=today.year-1)

    elif rng == 'last_month':
        last_month_end   = first_of_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        date_from = last_month_start
        date_to   = last_month_end
        label     = f"Last Month ({last_month_start.strftime('%b %Y')})"
        prev_from = last_month_start.replace(year=last_month_start.year-1)
        prev_to   = last_month_end.replace(year=last_month_end.year-1)

    elif rng == 'last_3m':
        date_from = (first_of_month - timedelta(days=62)).replace(day=1)
        date_to   = today
        label     = "Last 3 Months"
        prev_to   = date_from - timedelta(days=1)
        delta     = (date_to - date_from).days
        prev_from = prev_to - timedelta(days=delta)

    elif rng == 'last_6m':
        date_from = (first_of_month - timedelta(days=152)).replace(day=1)
        date_to   = today
        label     = "Last 6 Months"
        prev_to   = date_from - timedelta(days=1)
        delta     = (date_to - date_from).days
        prev_from = prev_to - timedelta(days=delta)

    elif rng == 'this_year':
        date_from = first_of_year
        date_to   = today
        label     = f"This Year ({today.year})"
        prev_from = first_of_year.replace(year=today.year-1)
        prev_to   = today.replace(year=today.year-1)

    elif rng == 'lifetime':
        date_from = date(2000, 1, 1)   # effectively all data
        date_to   = today
        label     = "All Time"
        prev_from = date(2000, 1, 1)
        prev_to   = today

    elif rng == 'custom':
        try:
            date_from = datetime.strptime(request.GET.get('from',''), '%Y-%m-%d').date()
            date_to   = datetime.strptime(request.GET.get('to',''), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            date_from = first_of_month
            date_to   = today
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        delta     = (date_to - date_from).days or 1
        prev_to   = date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=delta)
        label     = f"{date_from.strftime('%d %b')} – {date_to.strftime('%d %b %Y')}"

    else:
        date_from = first_of_month
        date_to   = today
        label     = f"This Month ({today.strftime('%b %Y')})"
        prev_from = first_of_month.replace(year=first_of_month.year-1)
        prev_to   = today.replace(year=today.year-1)
        rng       = 'this_month'

    return date_from, date_to, label, prev_from, prev_to, rng


def paginate_qs(qs, request):
    """Return (page_items, has_more, next_page) for a queryset."""
    try:
        page = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    start = (page - 1) * PAGE_SIZE
    end   = start + PAGE_SIZE
    items = list(qs[start:end + 1])   # fetch one extra to detect has_more
    has_more = len(items) > PAGE_SIZE
    if has_more:
        items = items[:PAGE_SIZE]
    return items, has_more, page + 1 if has_more else None


# ── Helpers ──────────────────────────────────────────────────────
def get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None

def get_godown(request):
    p = get_profile(request.user)
    return p.godown if p else None

# ── Decorators ────────────────────────────────────────────────────
def login_req(fn):
    @wraps(fn)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not get_profile(request.user):
            return redirect('login')
        return fn(request, *args, **kwargs)
    return wrapper

def admin_req(fn):
    @wraps(fn)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        p = get_profile(request.user)
        if not p or not p.is_admin:
            messages.error(request, 'Admin access required.')
            return redirect('sales_list')
        return fn(request, *args, **kwargs)
    return wrapper


# ── Auth ─────────────────────────────────────────────────────────
def login_view(request):
    if request.user.is_authenticated and get_profile(request.user):
        p = get_profile(request.user)
        return redirect('dashboard' if p.is_admin else 'sales_list')
    error = None
    if request.method == 'POST':
        user = authenticate(request,
                            username=request.POST.get('username','').strip(),
                            password=request.POST.get('password',''))
        if user and get_profile(user):
            login(request, user)
            nxt = request.GET.get('next', '/')
            p = get_profile(user)
            return redirect(nxt if nxt not in ('/login/','/') else
                           ('/' if p.is_admin else '/sales/'))
        error = 'Invalid username or password.'
    return render(request, 'godown/login.html', {'error': error})

def logout_view(request):
    logout(request)
    return redirect('login')


# ── Context helper — shared across all views ──────────────────────
def ctx(request, extra=None):
    """Return base context dict with profile and godown."""
    p = get_profile(request.user)
    d = {'profile': p, 'godown': p.godown if p else None}
    if extra:
        d.update(extra)
    return d


# ── User Management ───────────────────────────────────────────────
@admin_req
def user_list(request):
    godown = get_godown(request)
    users = User.objects.filter(profile__godown=godown).select_related('profile')
    user_count = users.count()
    return render(request, 'godown/users.html', ctx(request, {
        'active': 'users',
        'users_data': [{'user': u, 'profile': u.profile} for u in users],
        'user_count': user_count,
        'max_users': MAX_USERS_PER_GODOWN,
        'slots_remaining': max(0, MAX_USERS_PER_GODOWN - user_count),
    }))

@admin_req
def add_user(request):
    godown = get_godown(request)
    # Check user limit
    current_count = User.objects.filter(profile__godown=godown).count()
    if current_count >= 5:
        messages.error(request, f'User limit reached. Maximum {MAX_USERS_PER_GODOWN} users allowed per godown. '
                       f'Contact support to increase the limit.')
        return redirect('user_list')

    if request.method == 'POST':
        # Re-check limit on POST (race condition guard)
        if User.objects.filter(profile__godown=godown).count() >= 5:
            messages.error(request, f'User limit reached (max {MAX_USERS_PER_GODOWN}).')
            return redirect('user_list')
        username = request.POST.get('username','').strip()
        password = request.POST.get('password','')
        full_name = request.POST.get('full_name','').strip()
        role = request.POST.get('role','limited')
        phone = request.POST.get('phone','').strip()
        email = request.POST.get('email','').strip()
        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
        elif len(password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
        else:
            parts = full_name.split() if full_name else ['']
            u = User.objects.create_user(username=username, password=password,
                                          email=email, first_name=parts[0],
                                          last_name=' '.join(parts[1:]))
            UserProfile.objects.create(user=u, godown=godown, role=role, phone=phone)
            messages.success(request, f'User "{username}" created.')
            return redirect('user_list')
    user_count = User.objects.filter(profile__godown=godown).count()
    return render(request, 'godown/add_user.html', ctx(request, {
        'active': 'users',
        'user_count': user_count,
        'max_users': 5,
        'slots_remaining': max(0, 5 - user_count),
    }))

@admin_req
def edit_user(request, pk):
    godown = get_godown(request)
    target = get_object_or_404(User, pk=pk, profile__godown=godown)
    profile = target.profile
    if request.method == 'POST':
        full_name = request.POST.get('full_name','').strip()
        parts = full_name.split() if full_name else ['']
        target.first_name = parts[0]
        target.last_name = ' '.join(parts[1:])
        target.email = request.POST.get('email','')
        pw = request.POST.get('password','').strip()
        if pw:
            if len(pw) < 6:
                messages.error(request, 'Password too short.')
                return render(request, 'godown/add_user.html',
                              ctx(request, {'active':'users','target':target,'edit_profile':profile}))
            target.set_password(pw)
        target.save()
        profile.role = request.POST.get('role','limited')
        profile.phone = request.POST.get('phone','')
        profile.save()
        messages.success(request, 'User updated.')
        return redirect('user_list')
    return render(request, 'godown/add_user.html',
                  ctx(request, {'active':'users','target':target,'edit_profile':profile}))

@admin_req
def delete_user(request, pk):
    godown = get_godown(request)
    target = get_object_or_404(User, pk=pk, profile__godown=godown)
    if target == request.user:
        messages.error(request, 'Cannot delete your own account.')
    else:
        name = target.username
        target.delete()
        messages.success(request, f'User "{name}" deleted.')
    return redirect('user_list')


# ── Dashboard ─────────────────────────────────────────────────────
@login_req
def dashboard(request):
    godown = get_godown(request)
    today  = timezone.now().date()

    # Date range — user-selectable
    date_from, date_to, range_label, prev_from, prev_to, rng = get_date_range(request)

    products  = list(Product.objects.filter(godown=godown))
    all_sales = list(Sale.objects.filter(godown=godown,
                     date__gte=date_from, date__lte=date_to
                     ).select_related('customer').prefetch_related('items__product'))
    all_grns  = list(StockIn.objects.filter(godown=godown,
                     date__gte=date_from, date__lte=date_to
                     ).prefetch_related('items','landing_expenses'))
    all_pos   = list(PurchaseOrder.objects.filter(godown=godown
                     ).select_related('supplier').prefetch_related('po_items'))

    from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Q
    from django.db.models.functions import Coalesce

    # ── All aggregates in minimal DB queries ─────────────────────
    # Revenue for selected range
    rev_agg = SaleItem.objects.filter(
        sale__godown=godown, sale__date__gte=date_from, sale__date__lte=date_to
    ).aggregate(total=Coalesce(Sum(
        ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
    ), 0, output_field=DecimalField()))
    sales_total = rev_agg['total']

    # Previous period revenue
    prev_rev_agg = SaleItem.objects.filter(
        sale__godown=godown, sale__date__gte=prev_from, sale__date__lte=prev_to
    ).aggregate(total=Coalesce(Sum(
        ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
    ), 0, output_field=DecimalField()))
    prev_revenue = prev_rev_agg['total']
    rev_delta_pct = None
    if prev_revenue > 0:
        rev_delta_pct = round((float(sales_total) - float(prev_revenue)) / float(prev_revenue) * 100, 1)

    # COGS for range
    cogs_agg = StockInItem.objects.filter(
        stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to
    ).aggregate(total=Coalesce(Sum(
        ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
    ), 0, output_field=DecimalField()))
    land_agg = LandingExpense.objects.filter(
        stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to
    ).aggregate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
    range_cogs = cogs_agg['total'] + land_agg['total']

    # Expenses for range
    exp_agg = Expense.objects.filter(
        godown=godown, date__gte=date_from, date__lte=date_to
    ).aggregate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
    range_exp    = exp_agg['total']
    range_profit = sales_total - range_cogs - range_exp

    # Receivables — DB aggregated: total billed - total received
    recv_agg = SaleItem.objects.filter(sale__godown=godown).aggregate(
        billed=Coalesce(Sum(
            ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
        ), 0, output_field=DecimalField())
    )
    recv_received = Sale.objects.filter(godown=godown).aggregate(
        received=Coalesce(Sum('amount_received'), 0, output_field=DecimalField())
    )
    total_receivable = max(Decimal('0'), recv_agg['billed'] - recv_received['received'])

    # Payables — DB aggregated: total GRN cost - advance paid - amount paid
    pay_items = StockInItem.objects.filter(stock_in__godown=godown).aggregate(
        total=Coalesce(Sum(
            ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
        ), 0, output_field=DecimalField())
    )
    pay_landing = LandingExpense.objects.filter(stock_in__godown=godown).aggregate(
        total=Coalesce(Sum('amount'), 0, output_field=DecimalField())
    )
    pay_paid = StockIn.objects.filter(godown=godown).aggregate(
        total=Coalesce(Sum('amount_paid'), 0, output_field=DecimalField())
    )
    po_advance = PurchaseOrder.objects.filter(godown=godown).aggregate(
        total=Coalesce(Sum('advance_paid'), 0, output_field=DecimalField())
    )
    total_payable = max(Decimal('0'),
        pay_items['total'] + pay_landing['total'] - pay_paid['total'] - po_advance['total'])

    # GST for range
    gst_agg = SaleItem.objects.filter(
        sale__godown=godown, sale__date__gte=date_from, sale__date__lte=date_to
    ).aggregate(total=Coalesce(Sum(
        ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
    ), 0, output_field=DecimalField()))
    taxable_sales = gst_agg['total']
    gst_collected = (taxable_sales * godown.gst_rate / 100).quantize(Decimal('0.01'))
    purchase_items_agg = StockInItem.objects.filter(
        stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to
    ).aggregate(total=Coalesce(Sum(
        ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
    ), 0, output_field=DecimalField()))
    purchase_value = purchase_items_agg['total']
    itc      = (purchase_value * godown.gst_rate / 100).quantize(Decimal('0.01'))
    net_gst  = max(Decimal('0'), gst_collected - itc)

    stock_value  = sum(p.stock_qty * p.avg_cost for p in products)

    # Damage write-offs in range
    range_damage_writeoff = Decimal('0')
    try:
        from django.db.models import ExpressionWrapper as _EW2, F as _F2
        _dmg_agg = StockDamage.objects.filter(
            godown=godown, date__gte=date_from, date__lte=date_to
        ).aggregate(total=Coalesce(Sum(
            _EW2(_F2('qty_sqft') * _F2('cost_rate'), output_field=DecimalField())
        ), Decimal('0'), output_field=DecimalField()))
        range_damage_writeoff = _dmg_agg['total'] or Decimal('0')
        # Include in COGS (damage is a cost against profit)
        range_cogs   = range_cogs + range_damage_writeoff
        range_profit = sales_total - range_cogs - range_exp
    except Exception:
        pass

    low_stock    = [p for p in products if p.stock_qty < p.min_stock][:5]
    # Recent sales — fetch fresh with prefetch, limit at DB level
    recent_sales = list(Sale.objects.filter(godown=godown)
                        .select_related('customer')
                        .prefetch_related('items__product')
                        .order_by('-date','-created_at')[:5])
    # Due sales — fetch only unpaid with due date this week
    due_sales    = list(Sale.objects.filter(
                        godown=godown, amount_received__lt=F('amount_received') + 1,
                        due_date__gte=today, due_date__lte=today + timedelta(days=7)
                   ).select_related('customer').prefetch_related('items')
                   .order_by('due_date')[:5])
    upcoming_pos = sorted(
        [p for p in all_pos if p.status in ('pending','partial')
         and p.expected_arrival and p.expected_arrival >= today],
        key=lambda p: p.expected_arrival)[:5]

    return render(request, 'godown/dashboard.html', ctx(request, {
        'active':'dashboard',
        # Range
        'range': rng, 'range_label': range_label,
        'date_from': date_from, 'date_to': date_to,
        # KPIs
        'stock_value': stock_value,
        'sales_total': sales_total,
        'total_receivable': total_receivable,
        'total_payable': total_payable,
        'overdue_payables': sum(1 for g in
            StockIn.objects.filter(godown=godown).prefetch_related('items','landing_expenses')
            if g.balance > 0),
        # P&L
        'range_revenue': sales_total, 'range_cogs': range_cogs,
        'range_expenses': range_exp,  'range_profit': range_profit,
        'rev_delta_pct': rev_delta_pct,
        # GST
        'gst_collected': gst_collected, 'itc_this_month': itc,
        'net_gst_payable': net_gst, 'gst_rate': godown.gst_rate,
        # Lists
        'low_stock': low_stock, 'recent_sales': recent_sales,
        'due_sales': due_sales, 'upcoming_pos': upcoming_pos,
        'ranges': [('this_month','This Month'),('last_month','Last Month'),('last_3m','Last 3 Months'),('last_6m','Last 6 Months'),('this_year','This Year'),('lifetime','All Time')],
        'range_damage_writeoff': range_damage_writeoff,
    }))


# ── Customers ─────────────────────────────────────────────────────
@login_req
def customers(request):
    from django.db.models import Sum, Count, OuterRef, Subquery, DecimalField, FloatField
    from django.db.models.functions import Coalesce
    godown = get_godown(request)

    from django.db.models import Sum, Count, ExpressionWrapper, DecimalField, FloatField, F, When, Case, Value
    from django.db.models.functions import Coalesce
    customers_qs = Customer.objects.filter(godown=godown).annotate(
        sale_count=Count('sales', distinct=True),
        # Total billed = sum of (qty * rate) across all sale items
        db_total_billed=Coalesce(Sum(
            ExpressionWrapper(F('sales__items__qty_sqm') * F('sales__items__rate_per_sqm'),
                              output_field=DecimalField())
        ), 0, output_field=DecimalField()),
        db_total_received=Coalesce(Sum('sales__amount_received', output_field=DecimalField()),
                                    0, output_field=DecimalField()),
    ).order_by('name')
    # Attach outstanding as a computed attribute in Python (billed - received)
    for c in customers_qs:
        c.db_outstanding = c.db_total_billed - c.db_total_received
    # Which customers have sales — cannot delete these
    used_customer_pks = set(
        Sale.objects.filter(godown=godown).values_list('customer_id', flat=True)
    )
    return render(request, 'godown/customers.html', ctx(request, {
        'active':'customers',
        'customers': customers_qs,
        'used_pks': used_customer_pks,
    }))

@login_req
def add_customer(request):
    godown = get_godown(request)
    if request.method == 'POST':
        Customer.objects.create(
            godown=godown,
            name=request.POST['name'],
            phone=request.POST.get('phone',''),
            location=request.POST.get('location',''),
            state=request.POST.get('state','Kerala'),
            gst_number=request.POST.get('gst_number',''),
            credit_limit=request.POST.get('credit_limit',0) or 0,
        )
        messages.success(request, 'Customer added.')
        return redirect('customers')
    return render(request, 'godown/add_customer.html', ctx(request, {'active':'customers'}))

@login_req
def edit_customer(request, pk):
    godown = get_godown(request)
    c = get_object_or_404(Customer, pk=pk, godown=godown)
    if request.method == 'POST':
        c.name=request.POST['name']; c.phone=request.POST.get('phone','')
        c.location=request.POST.get('location',''); c.state=request.POST.get('state','Kerala')
        c.gst_number=request.POST.get('gst_number','')
        c.credit_limit=request.POST.get('credit_limit',0) or 0
        c.save()
        messages.success(request, 'Customer updated.')
        return redirect('customers')
    return render(request, 'godown/add_customer.html', ctx(request, {'active':'customers','customer':c}))

@login_req
def customer_statement(request, pk):
    godown = get_godown(request)
    customer = get_object_or_404(Customer, pk=pk, godown=godown)
    sales = Sale.objects.filter(customer=customer, godown=godown).prefetch_related('items__product','payments').order_by('date')
    ledger = []
    balance = Decimal('0')
    for sale in sales:
        balance += sale.grand_total
        ledger.append({'date':sale.date,'type':'invoice','ref':sale.bill_number,'debit':sale.grand_total,'credit':Decimal('0'),'balance':balance,'sale':sale})
        for p in sale.payments.order_by('date'):
            balance -= p.amount
            ledger.append({'date':p.date,'type':'payment','ref':p.reference or p.get_mode_display(),'debit':Decimal('0'),'credit':p.amount,'balance':balance,'sale':sale})
    total_billed = sum(s.grand_total for s in sales)
    total_paid   = sum(s.amount_received for s in sales)
    return render(request, 'godown/customer_statement.html', ctx(request, {
        'active':'customers','customer':customer,'ledger':ledger,'sales':sales,
        'total_billed':total_billed,'total_paid':total_paid,
        'total_balance':total_billed-total_paid,
        'overdue_amt': sum(s.balance for s in sales if s.is_overdue),
    }))


# ── Suppliers ─────────────────────────────────────────────────────
@login_req
def suppliers(request):
    from django.db.models import Count, DecimalField, Sum, ExpressionWrapper, F
    from django.db.models.functions import Coalesce
    godown = get_godown(request)
    supplier_type_filter = request.GET.get('type', '')
    suppliers_qs = Supplier.objects.filter(godown=godown).annotate(
        grn_count=Count('purchase_orders', distinct=True),
    ).order_by('name')
    if supplier_type_filter in ('material', 'service', 'both'):
        suppliers_qs = suppliers_qs.filter(supplier_type=supplier_type_filter)

    from godown.models import StockIn as _SI, PurchaseOrder as _PO
    for sup in suppliers_qs:
        # Material payable — items only, never landing expenses
        grns = _SI.objects.filter(supplier=sup, godown=godown).prefetch_related('items')
        pos  = _PO.objects.filter(supplier=sup, godown=godown)
        items_total = sum(i.amount for g in grns for i in g.items.all())
        paid_inr    = sum(g.amount_paid_inr for g in grns)
        advance_inr = sum(p.advance_paid_inr for p in pos)
        sup.db_total_payable = items_total - paid_inr - advance_inr

        # Service payable — landing expenses where this supplier is the vendor
        sup.db_service_payable = sup.total_service_payable

        # Combined — what shows in the main Payable column
        sup.db_combined_payable = sup.db_total_payable + sup.db_service_payable

    used_supplier_pks = set(
        StockIn.objects.filter(godown=godown).values_list('supplier_id', flat=True)
    ) | set(
        PurchaseOrder.objects.filter(godown=godown).values_list('supplier_id', flat=True)
    ) | set(
        LandingExpense.objects.filter(stock_in__godown=godown).exclude(vendor=None).values_list('vendor_id', flat=True)
    )
    return render(request, 'godown/suppliers.html', ctx(request, {
        'active':'suppliers',
        'suppliers': suppliers_qs,
        'used_pks': used_supplier_pks,
        'supplier_type_filter': supplier_type_filter,
    }))

@login_req
def add_supplier(request):
    godown = get_godown(request)
    if request.method == 'POST':
        Supplier.objects.create(godown=godown, name=request.POST['name'],
            supplier_type=request.POST.get('supplier_type', 'material'),
            phone=request.POST.get('phone',''), city=request.POST.get('city',''),
            state=request.POST.get('state',''), gst_number=request.POST.get('gst_number',''),
            address=request.POST.get('address',''), species_supplied=request.POST.get('species_supplied',''))
        messages.success(request, 'Supplier added.')
        return redirect('suppliers')
    return render(request, 'godown/add_supplier.html', ctx(request, {'active':'suppliers'}))

@login_req
def edit_supplier(request, pk):
    godown = get_godown(request)
    s = get_object_or_404(Supplier, pk=pk, godown=godown)
    if request.method == 'POST':
        s.name=request.POST['name']; s.supplier_type=request.POST.get('supplier_type', s.supplier_type)
        s.phone=request.POST.get('phone','')
        s.city=request.POST.get('city',''); s.state=request.POST.get('state','')
        s.gst_number=request.POST.get('gst_number',''); s.address=request.POST.get('address','')
        s.species_supplied=request.POST.get('species_supplied',''); s.save()
        messages.success(request, 'Supplier updated.')
        return redirect('suppliers')
    return render(request, 'godown/add_supplier.html', ctx(request, {'active':'suppliers','supplier':s}))


# ── Products ──────────────────────────────────────────────────────
@login_req
def products(request):
    godown = get_godown(request)
    used_product_pks = set(
        SaleItem.objects.filter(sale__godown=godown).values_list('product_id', flat=True)
    ) | set(
        StockInItem.objects.filter(stock_in__godown=godown).values_list('product_id', flat=True)
    )
    return render(request, 'godown/products.html', ctx(request, {
        'active':'products',
        'products': Product.objects.filter(godown=godown),
        'used_pks': used_product_pks,
    }))

@login_req
def add_product(request):
    godown = get_godown(request)
    if request.method == 'POST':
        errors = []
        species   = request.POST.get('species', '').strip()
        sale_name = request.POST.get('sale_name', '').strip()
        if not species:
            errors.append('Purchase Name (Species) is required.')
        if not sale_name:
            errors.append('Selling Name is required.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_product.html', ctx(request, {
                'active':'products',
                'thickness_choices': LookupValue.choices_for(godown, 'thickness'),
                'cut_choices':       LookupValue.choices_for(godown, 'cut_type'),
                'finish_choices':    LookupValue.choices_for(godown, 'finish'),
                'default_thickness': LookupValue.default_for(godown, 'thickness'),
                'default_cut':       LookupValue.default_for(godown, 'cut_type'),
                'default_finish':    LookupValue.default_for(godown, 'finish'),
                'form_errors':       errors,
                'posted':            request.POST,
            }))
        sl  = Decimal(request.POST.get('sheet_length', 8) or 8)
        sw  = Decimal(request.POST.get('sheet_width',  4) or 4)
        auto_sqm = (sl * sw * Decimal('0.0929')).quantize(Decimal('0.0001'))
        ov_raw   = request.POST.get('sheet_sqm_override', '').strip()
        override = Decimal(ov_raw).quantize(Decimal('0.0001')) if ov_raw else None
        Product.objects.create(
            godown=godown, species=species,
            sale_name=sale_name,
            thickness=request.POST.get('thickness','0.6mm'),
            cut_type=request.POST.get('cut_type','Flat Cut'),
            finish=request.POST.get('finish','Natural'),
            buy_rate=request.POST.get('buy_rate',0) or 0,
            sale_rate=request.POST.get('sale_rate',0) or 0,
            min_stock=request.POST.get('min_stock',500) or 500,
            hsn_code=request.POST.get('hsn_code','4408'),
            sheet_length=sl, sheet_width=sw,
            sheet_sqm=auto_sqm, sheet_sqm_override=override,
        )
        messages.success(request, 'Product added.')
        return redirect('products')
    godown = get_godown(request)
    return render(request, 'godown/add_product.html', ctx(request, {
        'active':'products',
        'thickness_choices': LookupValue.choices_for(godown, 'thickness'),
        'cut_choices':       LookupValue.choices_for(godown, 'cut_type'),
        'finish_choices':    LookupValue.choices_for(godown, 'finish'),
        'default_thickness': LookupValue.default_for(godown, 'thickness'),
        'default_cut':       LookupValue.default_for(godown, 'cut_type'),
        'default_finish':    LookupValue.default_for(godown, 'finish'),
    }))

@login_req
def edit_product(request, pk):
    godown = get_godown(request)
    p = get_object_or_404(Product, pk=pk, godown=godown)
    if request.method == 'POST':
        errors = []
        species   = request.POST.get('species', '').strip()
        sale_name = request.POST.get('sale_name', '').strip()
        if not species:
            errors.append('Purchase Name (Species) is required.')
        if not sale_name:
            errors.append('Selling Name is required.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_product.html', ctx(request, {
                'active':'products', 'product':p,
                'thickness_choices': LookupValue.choices_for(godown, 'thickness'),
                'cut_choices':       LookupValue.choices_for(godown, 'cut_type'),
                'finish_choices':    LookupValue.choices_for(godown, 'finish'),
                'form_errors':       errors,
            }))
        sl  = Decimal(request.POST.get('sheet_length', 8) or 8)
        sw  = Decimal(request.POST.get('sheet_width',  4) or 4)
        ov_raw = request.POST.get('sheet_sqm_override', '').strip()
        p.species=species; p.sale_name=sale_name
        p.thickness=request.POST.get('thickness','0.6mm')
        p.cut_type=request.POST.get('cut_type','Flat Cut'); p.finish=request.POST.get('finish','Natural')
        p.buy_rate=request.POST.get('buy_rate',0) or 0; p.sale_rate=request.POST.get('sale_rate',0) or 0
        p.min_stock=request.POST.get('min_stock',500) or 500; p.hsn_code=request.POST.get('hsn_code','4408')
        p.sheet_length=sl; p.sheet_width=sw
        p.sheet_sqm = (sl * sw * Decimal('0.0929')).quantize(Decimal('0.0001'))
        p.sheet_sqm_override = Decimal(ov_raw).quantize(Decimal('0.0001')) if ov_raw else None
        p.save()
        messages.success(request, 'Product updated.')
        return redirect('products')
    godown = get_godown(request)
    return render(request, 'godown/add_product.html', ctx(request, {
        'active':'products', 'product':p,
        'thickness_choices': LookupValue.choices_for(godown, 'thickness'),
        'cut_choices':       LookupValue.choices_for(godown, 'cut_type'),
        'finish_choices':    LookupValue.choices_for(godown, 'finish'),
    }))

@login_req
def product_api(request, pk):
    godown = get_godown(request)
    p = get_object_or_404(Product, pk=pk, godown=godown)
    return JsonResponse({'id':p.pk,'display_name':p.display_name,
        'sheet_length':float(p.sheet_length),'sheet_width':float(p.sheet_width),
        'sheet_sqm':float(p.effective_sheet_sqm),'sheet_sqm_override':float(p.sheet_sqm_override) if p.sheet_sqm_override else None,'buy_rate':float(p.buy_rate),
        'sale_rate':float(p.sale_rate),'stock_qty':float(p.stock_qty)})


# ── Purchase Orders ───────────────────────────────────────────────
@login_req
def po_list(request):
    godown = get_godown(request)
    qs     = PurchaseOrder.objects.filter(godown=godown).select_related('supplier').prefetch_related('po_items__product').order_by('-date','-created_at')

    if request.GET.get('format') == 'json':
        items, has_more, next_page = paginate_qs(qs, request)
        rows = []
        for p in items:
            rows.append({
                'pk': p.pk, 'po_number': p.po_number,
                'date': str(p.date), 'supplier': p.supplier.name,
                'expected_arrival': str(p.expected_arrival) if p.expected_arrival else '',
                'items': [{'product': i.product.display_name, 'qty_ordered': float(i.qty_sqm), 'qty_received': float(i.qty_received), 'is_fully': i.is_fully_received} for i in p.po_items.all()],
                'total_value': float(p.total_value),
                'advance_paid': float(p.advance_paid),
                'status': p.status, 'status_display': p.get_status_display(),
                'detail_url': f'/purchase-orders/{p.pk}/',
            })
        return JsonResponse({'rows': rows, 'has_more': has_more, 'next_page': next_page})

    first_page, has_more, _ = paginate_qs(qs, request)
    return render(request, 'godown/po_list.html', ctx(request, {'active':'po','pos':first_page,'has_more':has_more}))

@login_req
def add_po(request):
    godown = get_godown(request)
    suppliers_list = Supplier.objects.filter(godown=godown, supplier_type__in=['material','both'])
    products_list  = Product.objects.filter(godown=godown)
    if request.method == 'POST':
        # ── Parse items (same logic as _save_po_items) ───────────
        all_qtys = request.POST.getlist('qty_sqm[]')
        rates    = request.POST.getlist('rate_per_sqm[]')
        pids     = request.POST.getlist('product[]')
        n_items  = len(pids)
        two_per  = len(all_qtys) == n_items * 2
        errors   = []
        if not request.POST.get('supplier'):
            errors.append('Please select a supplier.')
        if not request.POST.get('date'):
            errors.append('Please enter a date.')
        line_items = []
        for i, pid in enumerate(pids):
            if not pid: continue
            qty_str = (all_qtys[i*2+1] or all_qtys[i*2]) if two_per else (all_qtys[i] if i < len(all_qtys) else '')
            try:
                qty  = Decimal(qty_str)  if qty_str  else Decimal('0')
                rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number.'); continue
            if qty <= 0:  errors.append(f'Item {i+1}: Quantity must be > 0.'); continue
            if rate <= 0: errors.append(f'Item {i+1}: Rate must be > 0.'); continue
            try:    product = products_list.get(pk=pid)
            except: errors.append(f'Item {i+1}: Product not found.'); continue
            line_items.append((product, qty, rate))
        if not line_items and not errors:
            errors.append('Add at least one item with product, quantity and rate.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_po.html', ctx(request, {
                'active':'po','suppliers':suppliers_list,'products':products_list
            }))

        # ── Preview ───────────────────────────────────────────────
        if not request.POST.get('confirmed'):
            supplier = suppliers_list.get(pk=request.POST['supplier'])
            total = sum(q*r for p,q,r in line_items)
            adv   = Decimal(request.POST.get('advance_paid',0) or 0)
            header = [
                ('PO Number', request.POST.get('po_number') or 'Auto'),
                ('Supplier',  supplier.name),
                ('Date',      request.POST.get('date')),
                ('Currency',  request.POST.get('currency','INR')),
            ]
            if request.POST.get('expected_arrival'):
                header.append(('Expected Arrival', request.POST.get('expected_arrival')))
            rows = [[p.display_name, f'{q:.4f} sq.m', f'₹{r:.2f}/sq.m', f'₹{q*r:,.2f}']
                    for p,q,r in line_items]
            totals = [('Total PO Value', f'₹{total:,.2f}', True)]
            if adv > 0:
                totals.append(('Advance Paid', f'₹{adv:,.2f}', False))
                totals.append(('Balance Payable', f'₹{max(Decimal(0),total-adv):,.2f}', False))
            return _preview_response(request,
                title=f'Purchase Order — {supplier.name}', icon='📋',
                header=header, items=rows,
                item_cols=['Product','Quantity','Rate','Amount'],
                totals=totals, confirm_url=request.path)

        # ── Confirmed — save ──────────────────────────────────────
        with transaction.atomic():
            custom_po = request.POST.get('po_number', '').strip()
            if custom_po:
                po_number = custom_po
                GodownSequence.next(godown, 'po')
            else:
                po_number = GodownSequence.format_number(godown, 'po')
            currency  = request.POST.get('currency', 'INR')
            exch_rate = Decimal(request.POST.get('advance_exchange_rate', '1') or '1')
            po = PurchaseOrder.objects.create(
                godown=godown, po_number=po_number,
                supplier_id=request.POST['supplier'],
                date=request.POST['date'],
                expected_arrival=request.POST.get('expected_arrival') or None,
                currency=currency,
                advance_paid=request.POST.get('advance_paid',0) or 0,
                advance_exchange_rate=exch_rate if currency=='USD' else Decimal('1'),
                advance_mode=request.POST.get('advance_mode','bank'),
                notes=request.POST.get('notes',''),
            )
            _save_po_items(request, po, products_list)
        messages.success(request, f'Purchase Order {po.po_number} created.')
        return redirect('po_list')
    return render(request, 'godown/add_po.html', ctx(request, {
        'active':'po','suppliers':suppliers_list,'products':products_list
    }))

def _save_po_items(request, po, products_list):
    units   = request.POST.getlist('qty_unit[]')
    pieces  = request.POST.getlist('pieces[]')
    lengths = request.POST.getlist('sheet_length[]')
    widths  = request.POST.getlist('sheet_width[]')
    rates   = request.POST.getlist('rate_per_sqm[]')
    # Each line item has TWO qty_sqm[] hidden inputs (sqm-section + pcs-section).
    # Only one will be non-empty depending on the selected unit.
    # Group them by line item: slot 0 and 1 belong to item 0, slot 2 and 3 to item 1, etc.
    all_qtys = request.POST.getlist('qty_sqm[]')
    products = request.POST.getlist('product[]')
    n_items  = len(products)
    # If two hidden inputs per item: all_qtys length = n_items * 2
    # If one hidden input per item (sqm-only mode): all_qtys length = n_items
    two_per_item = len(all_qtys) == n_items * 2

    for i, pid in enumerate(products):
        if not pid: continue
        if two_per_item:
            # Pick whichever of the two slots for this item is non-empty
            a = all_qtys[i * 2]     # sqm-section hidden
            b = all_qtys[i * 2 + 1] # pcs-section hidden
            qty_str = b if b else a
        else:
            qty_str = all_qtys[i] if i < len(all_qtys) else ''
        qty  = Decimal(qty_str) if qty_str else Decimal('0')
        rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
        if qty <= 0 or rate <= 0: continue
        unit = units[i] if i < len(units) else 'sqm'
        pcs  = Decimal(pieces[i]) if i < len(pieces) and pieces[i] else None
        l    = Decimal(lengths[i]) if i < len(lengths) and lengths[i] else None
        w    = Decimal(widths[i]) if i < len(widths) and widths[i] else None
        PurchaseOrderItem.objects.create(
            po=po, product_id=pid,
            qty_sqm=qty, rate_per_sqm=rate,
            qty_unit=unit, pieces=pcs, sheet_length=l, sheet_width=w,
        )

@login_req
def edit_po(request, pk):
    godown = get_godown(request)
    po = get_object_or_404(
        PurchaseOrder.objects.prefetch_related('po_items__product', 'grns__items'),
        pk=pk, godown=godown)
    products_list  = Product.objects.filter(godown=godown)
    suppliers_list = Supplier.objects.filter(godown=godown, supplier_type__in=['material','both'])

    # Build received qty map: product_pk → total qty received via GRNs against this PO
    received_qty = {}
    for grn in po.grns.all():
        for gi in grn.items.all():
            received_qty[gi.product_id] = received_qty.get(gi.product_id, Decimal('0')) + gi.qty_sqm

    if request.method == 'POST':
        errors = []
        if not request.POST.get('supplier'): errors.append('Please select a supplier.')
        if not request.POST.get('date'):     errors.append('Please enter a date.')

        # Parse items — same two-per-item logic as _save_po_items
        all_qtys = request.POST.getlist('qty_sqm[]')
        rates    = request.POST.getlist('rate_per_sqm[]')
        pids     = request.POST.getlist('product[]')
        units    = request.POST.getlist('qty_unit[]')
        pieces   = request.POST.getlist('pieces[]')
        lengths  = request.POST.getlist('sheet_length[]')
        widths   = request.POST.getlist('sheet_width[]')
        n_items  = len(pids)
        two_per  = len(all_qtys) == n_items * 2

        line_items = []
        for i, pid in enumerate(pids):
            if not pid: continue
            qty_str = (all_qtys[i*2+1] or all_qtys[i*2]) if two_per else (all_qtys[i] if i < len(all_qtys) else '')
            try:
                qty  = Decimal(qty_str)  if qty_str  else Decimal('0')
                rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number.'); continue
            if qty <= 0:  errors.append(f'Item {i+1}: Quantity must be > 0.'); continue
            if rate <= 0: errors.append(f'Item {i+1}: Rate must be > 0.'); continue
            try:    product = products_list.get(pk=pid)
            except: errors.append(f'Item {i+1}: Product not found.'); continue
            unit = units[i] if i < len(units) else 'sqm'
            pcs  = Decimal(pieces[i]) if i < len(pieces) and pieces[i] else None
            l    = Decimal(lengths[i]) if i < len(lengths) and lengths[i] else None
            w    = Decimal(widths[i]) if i < len(widths) and widths[i] else None
            # Safety: cannot order less than already received
            already = received_qty.get(int(pid), Decimal('0'))
            if qty < already:
                errors.append(
                    f'{product.display_name}: Cannot reduce to {qty:.4f} sq.m — '
                    f'{already:.4f} sq.m already received in GRN.'
                )
                continue
            line_items.append((product, qty, rate, unit, pcs, l, w))

        if not line_items and not errors:
            errors.append('Add at least one item.')

        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/edit_po.html', ctx(request, {
                'active': 'po', 'po': po,
                'suppliers': suppliers_list, 'products': products_list,
                'received_qty': received_qty, 'form_errors': errors,
            }))

        with transaction.atomic():
            po.supplier_id       = request.POST['supplier']
            po.date              = request.POST['date']
            po.expected_arrival  = request.POST.get('expected_arrival') or None
            po.currency          = request.POST.get('currency', 'INR')
            po.notes             = request.POST.get('notes', '')
            po.po_items.all().delete()
            for product, qty, rate, unit, pcs, l, w in line_items:
                PurchaseOrderItem.objects.create(
                    po=po, product=product,
                    qty_sqm=qty, rate_per_sqm=rate,
                    qty_unit=unit, pieces=pcs,
                    sheet_length=l, sheet_width=w,
                )
            po.save()
        messages.success(request, f'PO {po.po_number} updated.')
        return redirect('po_detail', pk=pk)

    return render(request, 'godown/edit_po.html', ctx(request, {
        'active': 'po', 'po': po,
        'suppliers': suppliers_list, 'products': products_list,
        'received_qty': received_qty,
    }))


@login_req
def po_detail(request, pk):
    godown = get_godown(request)
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier').prefetch_related('po_items__product','grns__items__product','grns__landing_expenses'),
        pk=pk, godown=godown)
    if request.method == 'POST':
        po.status = request.POST.get('status', po.status); po.save()
        messages.success(request, 'Status updated.')
        return redirect('po_detail', pk=pk)
    return render(request, 'godown/po_detail.html', ctx(request, {'active':'po','po':po}))

@login_req
def po_advance(request, pk):
    godown = get_godown(request)
    po = get_object_or_404(PurchaseOrder, pk=pk, godown=godown)
    if request.method == 'POST':
        amt = Decimal(request.POST.get('amount',0) or 0)
        po.advance_paid += amt; po.advance_mode = request.POST.get('mode', po.advance_mode); po.save()
        messages.success(request, f'Advance ₹{amt:,.0f} recorded.'); return redirect('po_detail', pk=pk)
    return render(request, 'godown/po_advance.html', ctx(request, {'active':'po','po':po}))


# ── Stock In ──────────────────────────────────────────────────────
@login_req
def stock_in_list(request):
    godown = get_godown(request)
    qs     = StockIn.objects.filter(godown=godown).select_related('supplier','po').prefetch_related('items__product','landing_expenses').order_by('-date','-created_at')

    if request.GET.get('format') == 'json':
        items, has_more, next_page = paginate_qs(qs, request)
        rows = []
        for g in items:
            advance_inr = float(g.po.advance_paid_inr) if g.po else 0
            rows.append({
                'pk': g.pk, 'grn_number': g.grn_number,
                'date': str(g.date), 'supplier': g.supplier.name,
                'po_number': g.po.po_number if g.po else '',
                'products': [f"{i.qty_display} @ ₹{i.rate_per_sqm}" for i in g.items.all()],
                'total_amount': float(g.total_amount),
                'landing_total': float(g.landing_expenses_total),
                'amount_paid': float(g.amount_paid_inr),
                'advance_inr': advance_inr,
                'balance': float(g.balance),
                'payment_mode': g.get_payment_mode_display(),
            })
        return JsonResponse({'rows': rows, 'has_more': has_more, 'next_page': next_page})

    first_page, has_more, _ = paginate_qs(qs, request)
    inventory = Product.objects.filter(godown=godown)
    return render(request, 'godown/stock_in.html', ctx(request, {
        'active':'stock_in','grns':first_page,'has_more':has_more,'inventory':inventory
    }))

@login_req
def add_stock_in(request):
    godown = get_godown(request)
    suppliers_list = Supplier.objects.filter(godown=godown, supplier_type__in=['material','both'])
    products_list  = Product.objects.filter(godown=godown)
    open_pos       = PurchaseOrder.objects.filter(godown=godown, status__in=['pending','partial']).select_related('supplier')
    if request.method == 'POST':
        # ── Parse and validate items ──────────────────────────────
        all_qtys = request.POST.getlist('qty_sqm[]')
        rates    = request.POST.getlist('rate_per_sqm[]')
        pids     = request.POST.getlist('product[]')
        n_items  = len(pids)
        two_per  = len(all_qtys) == n_items * 2
        errors   = []
        if not request.POST.get('supplier'):
            errors.append('Please select a supplier.')
        if not request.POST.get('date'):
            errors.append('Please enter a date.')
        line_items = []
        for i, pid in enumerate(pids):
            if not pid: continue
            qty_str = (all_qtys[i*2+1] or all_qtys[i*2]) if two_per else (all_qtys[i] if i < len(all_qtys) else '')
            try:
                qty  = Decimal(qty_str)  if qty_str  else Decimal('0')
                rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number.'); continue
            if qty <= 0:  errors.append(f'Item {i+1}: Quantity must be > 0.'); continue
            if rate <= 0: errors.append(f'Item {i+1}: Rate must be > 0.'); continue
            try:    product = products_list.get(pk=pid)
            except: errors.append(f'Item {i+1}: Product not found.'); continue
            line_items.append((product, qty, rate))
        if not line_items and not errors:
            errors.append('Add at least one item with product, quantity and rate.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_stock_in.html', ctx(request, {
                'active':'stock_in','suppliers':suppliers_list,'products':products_list,
                'open_pos':open_pos,
                'landing_categories': LookupValue.choices_for(godown, 'landing_expense_category'),
                'service_vendors': Supplier.objects.filter(godown=godown, supplier_type__in=['service','both']),
            }))

        # ── Preview ───────────────────────────────────────────────
        if not request.POST.get('confirmed'):
            supplier = suppliers_list.get(pk=request.POST['supplier'])
            items_total = sum(q*r for p,q,r in line_items)
            # Landing expenses
            exp_cats    = request.POST.getlist('exp_cat[]')
            exp_amts    = request.POST.getlist('exp_amt[]')
            exp_vendors = request.POST.getlist('exp_vendor[]')
            landing_total = Decimal('0')
            landing_rows  = []
            for j, cat in enumerate(exp_cats):
                amt_str = exp_amts[j] if j < len(exp_amts) else ''
                if not cat or not amt_str: continue
                try: amt = Decimal(amt_str)
                except: continue
                if amt <= 0: continue
                landing_total += amt
                vendor_id = exp_vendors[j] if j < len(exp_vendors) else ''
                vendor_label = ''
                if vendor_id:
                    try: vendor_label = f' → {Supplier.objects.get(pk=vendor_id).name}'
                    except: pass
                landing_rows.append([f'{cat}{vendor_label}', '', '', f'₹{amt:,.2f}'])
            grand = items_total + landing_total
            amtp  = Decimal(request.POST.get('amount_paid',0) or 0)
            header = [
                ('GRN Number', request.POST.get('grn_number') or 'Auto'),
                ('Supplier',   supplier.name),
                ('Date',       request.POST.get('date')),
                ('Payment',    request.POST.get('payment_mode','credit').title()),
            ]
            if request.POST.get('invoice_number'):
                header.append(('Supplier Invoice', request.POST.get('invoice_number')))
            rows = [[p.display_name, f'{q:.4f} sq.m', f'₹{r:.2f}/sq.m', f'₹{q*r:,.2f}']
                    for p,q,r in line_items] + landing_rows
            totals = [
                ('Items Total', f'₹{items_total:,.2f}', False),
            ]
            if landing_total > 0:
                totals.append(('Landing Expenses', f'₹{landing_total:,.2f}', False))
            totals.append(('Grand Total', f'₹{grand:,.2f}', True))
            if amtp > 0:
                totals.append(('Amount Paid', f'₹{amtp:,.2f}', False))
                totals.append(('Balance Payable', f'₹{max(Decimal(0),grand-amtp):,.2f}', False))
            return _preview_response(request,
                title=f'GRN — {supplier.name}', icon='📦',
                header=header, items=rows,
                item_cols=['Product / Expense', 'Quantity', 'Rate', 'Amount'],
                totals=totals, confirm_url=request.path)

        # ── Confirmed — save ──────────────────────────────────────
        with transaction.atomic():
            custom_grn = request.POST.get('grn_number', '').strip()
            if custom_grn:
                grn_number = custom_grn
                GodownSequence.next(godown, 'grn')
            else:
                grn_number = GodownSequence.format_number(godown, 'grn')
            po_id = request.POST.get('po') or None
            pay_currency = request.POST.get('payment_currency', 'INR')
            pay_rate = Decimal(request.POST.get('exchange_rate', '1') or '1')
            grn = StockIn.objects.create(
                godown=godown, grn_number=grn_number,
                supplier_id=request.POST['supplier'], po_id=po_id,
                date=request.POST['date'],
                invoice_number=request.POST.get('invoice_number',''),
                vehicle_number=request.POST.get('vehicle_number',''),
                amount_paid=request.POST.get('amount_paid',0) or 0,
                payment_currency=pay_currency,
                exchange_rate=pay_rate if pay_currency=='USD' else Decimal('1'),
                payment_mode=request.POST.get('payment_mode','credit'),
                notes=request.POST.get('notes',''),
            )
            si_items = _save_grn_items(request, grn, products_list)
            _save_landing_expenses(request, grn)
            _recalc_landed_rates(grn, si_items)
            if po_id:
                po = PurchaseOrder.objects.get(pk=po_id)
                po.update_status_from_grns()
        messages.success(request, f'GRN {grn.grn_number} saved.')
        return redirect('stock_in_list')
    return render(request, 'godown/add_stock_in.html', ctx(request, {
        'active':'stock_in','suppliers':suppliers_list,'products':products_list,'open_pos':open_pos,
        'landing_categories': LookupValue.choices_for(godown, 'landing_expense_category'),
        'service_vendors': Supplier.objects.filter(godown=godown, supplier_type__in=['service','both']),
    }))

def _save_grn_items(request, grn, products_list):
    units  = request.POST.getlist('qty_unit[]')
    pieces = request.POST.getlist('pieces[]')
    lengths= request.POST.getlist('sheet_length[]')
    widths = request.POST.getlist('sheet_width[]')
    rates  = request.POST.getlist('rate_per_sqm[]')
    racks  = request.POST.getlist('rack[]')
    # Each line item has TWO qty_sqm[] hidden inputs (sqm-section + pcs-section).
    all_qtys = request.POST.getlist('qty_sqm[]')
    products = request.POST.getlist('product[]')
    n_items  = len(products)
    two_per_item = len(all_qtys) == n_items * 2

    created = []
    for i, pid in enumerate(products):
        if not pid: continue
        if two_per_item:
            a = all_qtys[i * 2]
            b = all_qtys[i * 2 + 1]
            qty_str = b if b else a
        else:
            qty_str = all_qtys[i] if i < len(all_qtys) else ''
        qty  = Decimal(qty_str) if qty_str else Decimal('0')
        rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
        if qty <= 0 or rate <= 0: continue
        product = products_list.get(pk=pid)
        unit = units[i] if i < len(units) else 'sqm'
        pcs  = Decimal(pieces[i]) if i < len(pieces) and pieces[i] else None
        l    = Decimal(lengths[i]) if i < len(lengths) and lengths[i] else None
        w    = Decimal(widths[i]) if i < len(widths) and widths[i] else None
        product.update_avg_cost(qty, rate)
        si_item = StockInItem.objects.create(
            stock_in=grn, product=product,
            qty_sqm=qty, rate_per_sqm=rate, landed_rate=rate,
            rack_location=racks[i] if i < len(racks) else '',
            qty_unit=unit, pieces=pcs, sheet_length=l, sheet_width=w,
        )
        product.stock_qty += qty; product.save()
        created.append(si_item)
    return created

def _save_landing_expenses(request, grn):
    cats     = request.POST.getlist('exp_cat[]')
    descs    = request.POST.getlist('exp_desc[]')
    amts     = request.POST.getlist('exp_amt[]')
    paid_tos = request.POST.getlist('exp_paid_to[]')
    vendors  = request.POST.getlist('exp_vendor[]')
    for i, (cat, amt) in enumerate(zip(cats, amts)):
        if cat and amt:
            desc    = descs[i]    if i < len(descs)    else ''
            paid_to = paid_tos[i] if i < len(paid_tos) else ''
            vendor_id = vendors[i] if i < len(vendors) else ''
            LandingExpense.objects.create(
                stock_in=grn, category=cat, description=desc,
                amount=Decimal(amt), paid_to=paid_to,
                vendor_id=vendor_id if vendor_id else None,
            )

def _recalc_landed_rates(grn, si_items):
    total_landing = grn.landing_expenses_total
    if total_landing <= 0 or not si_items: return
    total_qty = sum(i.qty_sqm for i in si_items)
    if total_qty <= 0: return
    for si_item in si_items:
        share = (si_item.qty_sqm / total_qty) * total_landing
        si_item.landed_rate = si_item.rate_per_sqm + (share / si_item.qty_sqm)
        si_item.save(update_fields=['landed_rate'])
        p = si_item.product
        all_items = StockInItem.objects.filter(product=p)
        rv = sum(i.qty_sqm * (i.landed_rate or i.rate_per_sqm) for i in all_items)
        rq = sum(i.qty_sqm for i in all_items)
        if rq > 0: p.avg_cost = rv / rq; p.save(update_fields=['avg_cost'])


# ── Sales ─────────────────────────────────────────────────────────
@login_req
def sales_list(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    qs     = Sale.objects.filter(godown=godown).select_related('customer').prefetch_related('items__product').order_by('-date','-created_at')

    if request.GET.get('format') == 'json':
        items, has_more, next_page = paginate_qs(qs, request)
        rows = []
        for s in items:
            rows.append({
                'pk': s.pk, 'bill_number': s.bill_number,
                'date': str(s.date), 'customer': s.customer.name,
                'products': [i.product.display_name for i in s.items.all()],
                'total_amount': float(s.total_amount),
                'grand_total': float(s.grand_total),
                'gst_amount': float(s.gst_amount),
                'balance': float(s.balance),
                'status': s.status,
                'invoice_url': f'/sales/{s.pk}/invoice/',
                'payment_url': f'/sales/{s.pk}/payment/',
            })
        return JsonResponse({'rows': rows, 'has_more': has_more, 'next_page': next_page})

    # Aggregates from full qs (fast, no prefetch)
    all_sales_qs = Sale.objects.filter(godown=godown).prefetch_related('items')
    all_sales = list(all_sales_qs)
    t_tot = sum(s.total_amount for s in all_sales if s.date==today)
    m_s   = [s for s in all_sales if s.date.month==today.month and s.date.year==today.year]
    m_tot = sum(s.total_amount for s in m_s)

    first_page, has_more, _ = paginate_qs(qs, request)
    return render(request, 'godown/sales.html', ctx(request, {
        'active':'sales','sales':first_page,'has_more':has_more,
        'today_total':t_tot,'month_total':m_tot,
        'total_bills':len(all_sales),'avg_bill':(m_tot/len(m_s)) if m_s else 0
    }))

@login_req
def add_sale(request):
    godown = get_godown(request)
    from django.db.models import Sum, ExpressionWrapper, DecimalField, F
    from django.db.models.functions import Coalesce
    customers_list = Customer.objects.filter(godown=godown).annotate(
        db_total_billed=Coalesce(Sum(
            ExpressionWrapper(F('sales__items__qty_sqm') * F('sales__items__rate_per_sqm'),
                              output_field=DecimalField())
        ), 0, output_field=DecimalField()),
        db_total_received=Coalesce(
            Sum('sales__amount_received', output_field=DecimalField()),
            0, output_field=DecimalField()
        ),
    )
    for cust in customers_list:
        cust.computed_outstanding = max(Decimal("0"), cust.db_total_billed - cust.db_total_received)
    products_list = Product.objects.filter(godown=godown)
    if request.method == 'POST':
        sizes = request.POST.getlist('size[]') or ['']*20
        rates = request.POST.getlist('rate_per_sqm[]')
        qtys  = request.POST.getlist('qty_sqm[]')

        # ── PASS 1: Validate stock BEFORE opening a transaction ──
        # User enters sq.m — stored directly, no conversion needed
        stock_errors = []
        line_items   = []
        for i, pid in enumerate(request.POST.getlist('product[]')):
            if not pid: continue
            qty  = Decimal(qtys[i])  if i < len(qtys)  and qtys[i]  else Decimal('0')
            rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            if qty <= 0 or rate <= 0: continue

            try:
                product = products_list.get(pk=pid)
            except Product.DoesNotExist:
                stock_errors.append(f'Item {i+1}: Product not found.')
                continue

            if product.stock_qty <= 0:
                stock_errors.append(
                    f'{product.display_name}: Out of stock (0 sq.m available).'
                )
            elif qty > product.stock_qty:
                stock_errors.append(
                    f'{product.display_name}: Only {product.stock_qty:.4f} sq.m '
                    f'available, you entered {qty:.4f} sq.m.'
                )
            else:
                line_items.append((i, pid, product, qty, rate))

        if not line_items:
            if not stock_errors:
                stock_errors.append('Add at least one item with product, quantity and rate.')

        if stock_errors:
            for err in stock_errors:
                messages.error(request, err)
            return render(request, 'godown/add_sale.html', ctx(request, {
                'active':'sales','customers':customers_list,'products':products_list,'godown_obj':godown
            }))

        # ── PREVIEW: show summary before saving ──────────────────
        if not request.POST.get('confirmed'):
            customer = customers_list.get(pk=request.POST['customer'])
            sale_type = request.POST.get('sale_type', 'bill')
            gst_rate  = Decimal('0') if sale_type == 'cash_memo' else godown.gst_rate
            taxable   = sum(qty * rate for _, _, _, qty, rate in line_items)
            gst_amt   = (taxable * gst_rate / 100).quantize(Decimal('0.01'))
            grand     = taxable + gst_amt
            amr       = Decimal(request.POST.get('amount_received', 0) or 0)
            header = [
                ('Bill Number',   request.POST.get('bill_number') or 'Auto'),
                ('Type',          'Tax Invoice' if sale_type == 'bill' else 'Cash Memo'),
                ('Customer',      customer.name),
                ('Date',          request.POST.get('date')),
                ('Payment Mode',  request.POST.get('payment_mode','credit').title()),
            ]
            if request.POST.get('due_date'):
                header.append(('Due Date', request.POST.get('due_date')))
            items_rows = [
                [p.display_name,
                 f'{qty:.4f} sq.m',
                 f'₹{rate:.2f}/sq.m',
                 f'₹{qty*rate:,.2f}']
                for _, _, p, qty, rate in line_items
            ]
            totals = [
                ('Taxable Amount', f'₹{taxable:,.2f}', False),
                (f'GST ({gst_rate}%)', f'₹{gst_amt:,.2f}', False),
                ('Grand Total', f'₹{grand:,.2f}', True),
            ]
            if amr > 0:
                totals.append(('Amount Received', f'₹{amr:,.2f}', False))
                totals.append(('Balance Due', f'₹{max(Decimal(0), grand-amr):,.2f}', False))
            return _preview_response(
                request,
                title=f'New Sale to {customer.name}',
                icon='🧾',
                header=header,
                items=items_rows,
                item_cols=['Product', 'Quantity', 'Rate', 'Amount'],
                totals=totals,
                confirm_url=request.path,
            )
        with transaction.atomic():
            # Re-lock rows to prevent race condition between validation and save
            locked_products = {
                p.pk: p
                for p in Product.objects.select_for_update().filter(
                    pk__in=[item[1] for item in line_items]
                )
            }
            # Double-check stock hasn't changed since validation (qty is now sqft)
            race_errors = []
            for i, pid, product, qty, rate in line_items:
                locked = locked_products.get(int(pid))
                if locked and qty > locked.stock_qty:
                    race_errors.append(
                        f'{locked.display_name}: Stock changed — '
                        f'only {locked.stock_qty:.4f} sq.m available now.'
                    )
            if race_errors:
                for err in race_errors:
                    messages.error(request, err)
                return render(request, 'godown/add_sale.html', ctx(request, {
                    'active':'sales','customers':customers_list,'products':products_list,'godown_obj':godown
                }))

            sale_type   = request.POST.get('sale_type', 'bill')
            # Cash memos get their own sequence (CM-001, CM-002 ...)
            seq_type    = 'cash_memo' if sale_type == 'cash_memo' else 'sale'
            custom_num  = request.POST.get('bill_number', '').strip()
            if custom_num:
                bill_number = custom_num
                # Still advance the sequence so next auto number doesn't collide
                GodownSequence.next(godown, seq_type)
            else:
                bill_number = GodownSequence.format_number(godown, seq_type)
            sale = Sale.objects.create(
                godown=godown, bill_number=bill_number,
                sale_type=sale_type,
                customer_id=request.POST['customer'],
                date=request.POST['date'],
                due_date=request.POST.get('due_date') or None,
                transport=request.POST.get('transport',''),
                po_reference=request.POST.get('po_reference',''),
                amount_received=request.POST.get('amount_received',0) or 0,
                payment_mode=request.POST.get('payment_mode','credit'),
                # Cash memo has 0% GST (no formal invoice)
                gst_rate=Decimal('0') if sale_type == 'cash_memo' else godown.gst_rate,
                vehicle_number=request.POST.get('vehicle_number',''),
                transport_distance=request.POST.get('transport_distance') or None,
                transporter_id=request.POST.get('transporter_id',''),
                transporter_name=request.POST.get('transporter_name',''),
                ship_name=request.POST.get('ship_name',''),
                ship_addr1=request.POST.get('ship_addr1',''),
                ship_pincode=request.POST.get('ship_pincode',''),
                ship_state=request.POST.get('ship_state',''),
            )
            for i, pid, product, qty, rate in line_items:
                locked_product = locked_products.get(int(pid), product)
                cost_snap = locked_product.avg_cost
                fifo_batches, _ = get_fifo_grn(locked_product, qty, godown)
                if fifo_batches:
                    for si_item, take_qty in fifo_batches:
                        SaleItem.objects.create(sale=sale, product=locked_product,
                            size=sizes[i] if i<len(sizes) else '',
                            qty_sqm=take_qty, rate_per_sqm=rate,
                            cost_at_sale=si_item.landed_rate or cost_snap,
                            grn_source=si_item.stock_in)
                else:
                    SaleItem.objects.create(sale=sale, product=locked_product,
                        size=sizes[i] if i<len(sizes) else '',
                        qty_sqm=qty, rate_per_sqm=rate,
                        cost_at_sale=cost_snap, grn_source=None)
                locked_product.stock_qty -= qty
                locked_product.save()

        messages.success(request, f'Sale {sale.bill_number} saved.')
        return redirect('sales_list')
    # Preview next numbers for display (don't advance sequence)
    next_sale_num = GodownSequence.format_number.__func__ and None  # just peek
    from godown.models import GodownSequence as GS
    def peek_next(godown, seq_type):
        obj, _ = GS.objects.get_or_create(godown=godown, seq_type=seq_type, defaults={'last_num':0})
        prefix_map = {'sale': godown.invoice_prefix, 'cash_memo': 'CM',
                      'po': godown.po_prefix, 'grn': godown.grn_prefix, 'est': 'EST'}
        offset_map = {'sale': 1000, 'cash_memo': 0, 'po': 100, 'grn': 100, 'est': 100}
        num = obj.last_num + 1 + offset_map.get(seq_type, 0)
        return f"{prefix_map.get(seq_type,'')}-{num}"
    return render(request, 'godown/add_sale.html', ctx(request, {
        'active':'sales','customers':customers_list,'products':products_list,'godown_obj':godown,
        'next_bill_number': peek_next(godown, 'sale'),
        'next_cm_number':   peek_next(godown, 'cash_memo'),
    }))


# ── Receivables ───────────────────────────────────────────────────
@login_req
def receivables(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    all_sales = list(Sale.objects.filter(godown=godown).select_related('customer').prefetch_related('items').order_by('due_date'))
    outstanding = [s for s in all_sales if s.balance > 0]
    return render(request, 'godown/receivables.html', ctx(request, {
        'active':'receivables','outstanding':outstanding,
        'total_receivable': sum(s.balance for s in outstanding),
        'overdue_amount':   sum(s.balance for s in outstanding if s.is_overdue),
        'collected_month':  sum(s.amount_received for s in all_sales if s.date.month==today.month),
    }))

@login_req
def record_sale_payment(request, pk):
    godown = get_godown(request)
    sale = get_object_or_404(Sale, pk=pk, godown=godown)
    bank_accounts_qs = BankAccount.objects.filter(godown=godown, is_active=True)
    if request.method == 'POST':
        amt = Decimal(request.POST.get('amount', 0) or 0)
        if amt > 0:
            mode           = request.POST.get('mode', 'cash')
            bank_account_id = request.POST.get('bank_account') or None
            reference      = request.POST.get('reference', '')
            note           = request.POST.get('note', '')
            date           = request.POST.get('date', timezone.now().date())
            balance        = sale.balance

            Payment.objects.create(
                sale=sale, date=date, amount=amt, mode=mode,
                reference=reference, note=note, recorded_by=request.user,
            )
            sale.amount_received += amt
            sale.save()

            # Auto-create bank transaction if mode is non-cash and account selected
            if bank_account_id and mode not in ('cash', 'credit'):
                try:
                    bank_acc = BankAccount.objects.get(pk=bank_account_id, godown=godown)
                    BankTransaction.objects.create(
                        account=bank_acc,
                        date=date,
                        txn_type='credit',
                        category='sale_receipt',
                        amount=amt,
                        description=f'Receipt from {sale.customer.name} — {sale.bill_number}',
                        reference=reference,
                        sale=sale,
                        recorded_by=request.user,
                    )
                except BankAccount.DoesNotExist:
                    pass

            if amt > balance:
                messages.success(request, f'Payment ₹{amt:,.0f} recorded. ₹{amt-balance:,.0f} is advance credit.')
            else:
                messages.success(request, f'Payment ₹{amt:,.0f} recorded for {sale.bill_number}.')
        return redirect('customer_statement', pk=sale.customer.pk)
    return render(request, 'godown/record_sale_payment.html', ctx(request, {
        'active': 'receivables', 'sale': sale,
        'bank_accounts': bank_accounts_qs,
    }))


# ── Payables ──────────────────────────────────────────────────────
@login_req
def payables(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    all_grns = list(StockIn.objects.filter(godown=godown).select_related('supplier').prefetch_related('items','landing_expenses').order_by('date'))
    outstanding = [g for g in all_grns if g.balance > 0]
    return render(request, 'godown/payables.html', ctx(request, {
        'active':'payables','outstanding':outstanding,
        'total_payable': sum(g.balance for g in outstanding),
        'overdue_amount': sum(g.balance for g in outstanding if g.date+timedelta(days=30)<today),
        'paid_month': sum(g.amount_paid_inr for g in all_grns if g.date.month==today.month),
    }))

@login_req
def record_payment(request, pk):
    godown = get_godown(request)
    grn = get_object_or_404(StockIn, pk=pk, godown=godown)
    bank_accounts_qs = BankAccount.objects.filter(godown=godown, is_active=True)
    if request.method == 'POST':
        amt             = Decimal(request.POST.get('amount', 0) or 0)
        pay_currency    = request.POST.get('payment_currency', 'INR')
        pay_rate        = Decimal(request.POST.get('exchange_rate', '1') or '1')
        mode            = request.POST.get('payment_mode', 'bank')
        bank_account_id = request.POST.get('bank_account') or None
        reference       = request.POST.get('reference', '')
        date            = request.POST.get('date', str(timezone.now().date()))
        if amt <= 0:
            messages.error(request, 'Enter a valid payment amount.')
            return render(request, 'godown/record_payment.html', ctx(request, {
                'active': 'payables', 'grn': grn, 'bank_accounts': bank_accounts_qs,
            }))
        balance = grn.balance
        amt_inr = amt * pay_rate if pay_currency == 'USD' else amt
        grn.amount_paid += amt
        grn.payment_currency = pay_currency
        grn.exchange_rate    = pay_rate
        grn.save()

        # Auto-create bank transaction if account selected
        if bank_account_id:
            try:
                bank_acc = BankAccount.objects.get(pk=bank_account_id, godown=godown)
                BankTransaction.objects.create(
                    account=bank_acc,
                    date=date,
                    txn_type='debit',
                    category='supplier_payment',
                    amount=amt_inr,
                    description=f'Payment to {grn.supplier.name} — {grn.grn_number}',
                    reference=reference,
                    grn=grn,
                    recorded_by=request.user,
                )
            except BankAccount.DoesNotExist:
                pass

        if amt_inr > balance + Decimal('0.01'):
            messages.success(request, f'Payment recorded for {grn.grn_number}. '
                             f'₹{amt_inr-balance:,.0f} excess treated as advance credit.')
        else:
            messages.success(request, f'Payment ₹{amt_inr:,.0f} recorded for {grn.grn_number}.')
        return redirect('payables')
    return render(request, 'godown/record_payment.html', ctx(request, {
        'active': 'payables', 'grn': grn, 'bank_accounts': bank_accounts_qs,
    }))


# ── Expenses ──────────────────────────────────────────────────────
@login_req
def expenses(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    all_exp = list(Expense.objects.filter(godown=godown).order_by('-date'))
    month_exp = [e for e in all_exp if e.date.month==today.month and e.date.year==today.year]
    month_total = sum(e.amount for e in month_exp)
    cat_totals = {}
    for e in month_exp:
        cat_totals[e.get_category_display()] = cat_totals.get(e.get_category_display(),0) + e.amount
    pending = [e for e in all_exp if e.status=='pending']
    qs = Expense.objects.filter(godown=godown).order_by('-date','-created_at')
    if request.GET.get('format') == 'json':
        items, has_more, next_page = paginate_qs(qs, request)
        rows = []
        for e in items:
            rows.append({
                'pk': e.pk, 'date': str(e.date),
                'category': e.get_category_display(),
                'description': e.description, 'paid_to': e.paid_to,
                'amount': float(e.amount), 'payment_mode': e.get_payment_mode_display(),
                'status': e.status, 'status_display': e.get_status_display(),
                'bill_number': e.bill_number,
            })
        return JsonResponse({'rows': rows, 'has_more': has_more, 'next_page': next_page})

    first_page, has_more, _ = paginate_qs(qs, request)
    return render(request, 'godown/expenses.html', ctx(request, {
        'active':'expenses','expenses':first_page,'has_more':has_more,
        'month_total':month_total,'ytd_total':sum(e.amount for e in all_exp if e.date.year==today.year),
        'pending_count':len(pending),'pending_amount':sum(e.amount for e in pending),
        'cat_totals':cat_totals,'max_cat':max(cat_totals.values()) if cat_totals else 1,
        'largest_cat':max(cat_totals,key=cat_totals.get) if cat_totals else 'N/A',
    }))

@login_req
def add_expense(request):
    godown = get_godown(request)
    bank_accounts_qs = BankAccount.objects.filter(godown=godown, is_active=True)
    if request.method == 'POST':
        errors = []
        if not request.POST.get('category'):  errors.append('Please select a category.')
        if not request.POST.get('date'):       errors.append('Please enter a date.')
        if not request.POST.get('description','').strip(): errors.append('Description is required.')
        if not request.POST.get('paid_to','').strip(): errors.append('Paid To is required.')
        try:
            amt = Decimal(request.POST.get('amount','0') or '0')
            if amt <= 0: errors.append('Amount must be greater than 0.')
        except Exception:
            errors.append('Invalid amount.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_expense.html', ctx(request, {
                'active':'expenses',
                'expense_categories': LookupValue.choices_for(godown, 'expense_category'),
                'bank_accounts': bank_accounts_qs,
            }))

        if not request.POST.get('confirmed'):
            mode = request.POST.get('payment_mode','cash')
            bank_account_id = request.POST.get('bank_account','')
            bank_name = ''
            if bank_account_id:
                try:
                    ba = bank_accounts_qs.get(pk=bank_account_id)
                    bank_name = f' via {ba.account_name}'
                except BankAccount.DoesNotExist:
                    pass
            header = [
                ('Category',    request.POST.get('category','')),
                ('Date',        request.POST.get('date','')),
                ('Description', request.POST.get('description','')),
                ('Paid To',     request.POST.get('paid_to','')),
                ('Amount',      f"₹{amt:,.2f}"),
                ('Mode',        f"{mode.title()}{bank_name}"),
                ('Status',      request.POST.get('status','paid').title()),
            ]
            if request.POST.get('bill_number'):
                header.append(('Bill / Receipt No', request.POST.get('bill_number')))
            return _preview_response(request,
                title='Record Expense', icon='💸',
                header=header, items=[],
                item_cols=[], totals=[('Amount', f"₹{amt:,.2f}", True)],
                confirm_url=request.path)

        # Save expense
        expense = Expense.objects.create(
            godown=godown, category=request.POST['category'],
            date=request.POST['date'], description=request.POST.get('description',''),
            paid_to=request.POST.get('paid_to',''), amount=amt,
            payment_mode=request.POST.get('payment_mode','cash'),
            status=request.POST.get('status','paid'),
            bill_number=request.POST.get('bill_number',''),
        )
        # Auto-create bank transaction for non-cash paid expenses
        mode = request.POST.get('payment_mode','cash')
        bank_account_id = request.POST.get('bank_account','')
        if bank_account_id and mode not in ('cash', 'credit', 'pending'):
            try:
                bank_acc = BankAccount.objects.get(pk=bank_account_id, godown=godown)
                BankTransaction.objects.create(
                    account=bank_acc,
                    date=request.POST['date'],
                    txn_type='debit',
                    category='expense',
                    amount=amt,
                    description=f"{expense.get_category_display()} — {expense.description} (Paid to {expense.paid_to})",
                    reference=request.POST.get('bill_number',''),
                    expense=expense,
                    recorded_by=request.user,
                )
            except BankAccount.DoesNotExist:
                pass
        messages.success(request, 'Expense recorded.')
        return redirect('expenses')
    return render(request, 'godown/add_expense.html', ctx(request, {
        'active':'expenses',
        'expense_categories': LookupValue.choices_for(godown, 'expense_category'),
        'bank_accounts': bank_accounts_qs,
    }))


@login_req
def edit_expense(request, pk):
    godown  = get_godown(request)
    expense = get_object_or_404(Expense, pk=pk, godown=godown)
    bank_accounts_qs = BankAccount.objects.filter(godown=godown, is_active=True)
    if request.method == 'POST':
        expense.category     = request.POST['category']
        expense.date         = request.POST['date']
        expense.description  = request.POST.get('description', '')
        expense.paid_to      = request.POST.get('paid_to', '')
        expense.amount       = request.POST.get('amount', 0) or 0
        expense.payment_mode = request.POST.get('payment_mode', 'cash')
        expense.status       = request.POST.get('status', 'paid')
        expense.bill_number  = request.POST.get('bill_number', '')
        expense.save()
        messages.success(request, 'Expense updated.')
        return redirect('expenses')
    return render(request, 'godown/add_expense.html', ctx(request, {
        'active': 'expenses',
        'expense': expense,
        'expense_categories': LookupValue.choices_for(godown, 'expense_category'),
        'bank_accounts': bank_accounts_qs,
    }))


@login_req
def invoice_view(request, pk):
    godown = get_godown(request)
    sale = get_object_or_404(Sale.objects.select_related('customer').prefetch_related('items__product','payments'), pk=pk, godown=godown)
    return render(request, 'godown/invoice.html', {'sale':sale,'godown':godown,'print_mode':request.GET.get('print')=='1'})


# ── Analytics ─────────────────────────────────────────────────────
@admin_req
def analytics(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    date_from, date_to, range_label, prev_from, prev_to, rng = get_date_range(request)

    all_sales = list(Sale.objects.filter(godown=godown,
                     date__gte=date_from, date__lte=date_to).prefetch_related('items'))
    all_grns  = list(StockIn.objects.filter(godown=godown,
                     date__gte=date_from, date__lte=date_to).prefetch_related('items','landing_expenses'))
    all_exp   = list(Expense.objects.filter(godown=godown,
                     date__gte=date_from, date__lte=date_to))

    rev   = sum(s.total_amount for s in all_sales)
    cogs  = sum(g.total_amount for g in all_grns)
    exp   = sum(e.amount for e in all_exp)
    gross = rev - cogs
    net   = gross - exp
    gross_margin = round(float(gross) / float(rev) * 100, 1) if rev else 0
    net_margin   = round(float(net)   / float(rev) * 100, 1) if rev else 0

    # Previous period
    prev_sales = list(Sale.objects.filter(godown=godown,
                      date__gte=prev_from, date__lte=prev_to).prefetch_related('items'))
    prev_rev = sum(s.total_amount for s in prev_sales)
    rev_delta = round((float(rev)-float(prev_rev))/float(prev_rev)*100, 1) if prev_rev else None

    return render(request, 'godown/analytics.html', ctx(request, {
        'active':'analytics',
        'range': rng, 'range_label': range_label,
        'date_from': date_from, 'date_to': date_to,
        'revenue': rev, 'cogs': cogs, 'gross': gross,
        'expenses': exp, 'net': net,
        'gross_margin': gross_margin, 'net_margin': net_margin,
        'rev_delta': rev_delta,
        'customers': Customer.objects.filter(godown=godown),
        'ranges': [('this_month','This Month'),('last_month','Last Month'),('last_3m','Last 3 Months'),('last_6m','Last 6 Months'),('this_year','This Year'),('lifetime','All Time')],
    }))

@admin_req
def analytics_data(request):
    import calendar
    from django.db.models import Sum, DecimalField
    from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, Coalesce
    godown = get_godown(request)
    date_from, date_to, range_label, _, _, rng = get_date_range(request)
    days = (date_to - date_from).days + 1

    # ── Single aggregated queries — not one per bucket ────────────
    # Revenue: sum of (qty_sqft * rate_per_sqft) per SaleItem grouped by date trunc
    if days <= 31:
        trunc_fn = TruncDay
        fmt = '%d %b'
    elif days <= 92:
        trunc_fn = TruncWeek
        fmt = '%d %b'
    else:
        trunc_fn = TruncMonth
        fmt = '%b %y'

    # Revenue per period — SaleItem amounts aggregated
    from django.db.models import F, ExpressionWrapper
    rev_qs = (SaleItem.objects
              .filter(sale__godown=godown, sale__date__gte=date_from, sale__date__lte=date_to)
              .annotate(period=trunc_fn('sale__date'))
              .values('period')
              .annotate(total=Coalesce(Sum(
                  ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
              ), 0, output_field=DecimalField()))
              .order_by('period'))
    _dp = lambda x: x.date() if hasattr(x,'date') and callable(x.date) else x
    rev_map = {_dp(r['period']): float(r['total']) for r in rev_qs}

    # COGS per period — StockInItems + LandingExpenses
    cost_qs = (StockInItem.objects
               .filter(stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to)
               .annotate(period=trunc_fn('stock_in__date'))
               .values('period')
               .annotate(total=Coalesce(Sum(
                   ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
               ), 0, output_field=DecimalField()))
               .order_by('period'))
    cost_map = {_dp(r['period']): float(r['total']) for r in cost_qs}

    landing_qs = (LandingExpense.objects
                  .filter(stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to)
                  .annotate(period=trunc_fn('stock_in__date'))
                  .values('period')
                  .annotate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
                  .order_by('period'))
    for r in landing_qs:
        k = _dp(r['period'])
        cost_map[k] = cost_map.get(k, 0) + float(r['total'])

    # Build ordered bucket list
    buckets = []
    if days <= 31:
        d = date_from
        while d <= date_to:
            buckets.append({'month': d.strftime(fmt), 'revenue': rev_map.get(d, 0), 'cost': cost_map.get(d, 0)})
            d += timedelta(days=1)
    elif days <= 92:
        d = date_from
        while d <= date_to:
            # TruncWeek anchors to Monday — match that
            week_key = d - timedelta(days=d.weekday())
            buckets.append({'month': d.strftime(fmt), 'revenue': rev_map.get(week_key, 0), 'cost': cost_map.get(week_key, 0)})
            d += timedelta(days=7)
    else:
        d = date_from.replace(day=1)
        while d <= date_to:
            buckets.append({'month': d.strftime(fmt), 'revenue': rev_map.get(d, 0), 'cost': cost_map.get(d, 0)})
            if d.month == 12:
                d = d.replace(year=d.year+1, month=1, day=1)
            else:
                d = d.replace(month=d.month+1, day=1)

    # Species breakdown — single query with GROUP BY
    species_qs = (SaleItem.objects
                  .filter(sale__godown=godown, sale__date__gte=date_from, sale__date__lte=date_to)
                  .values('product__species')
                  .annotate(total=Coalesce(Sum(
                      ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
                  ), 0, output_field=DecimalField()))
                  .order_by('-total')[:6])
    sp_total = sum(float(r['total']) for r in species_qs) or 1
    species = [{'label': r['product__species'], 'value': round(float(r['total']) / sp_total * 100, 1)}
               for r in species_qs]

    # Expense buckets — single GROUP BY month query
    exp_qs = (Expense.objects
              .filter(godown=godown, date__gte=date_from, date__lte=date_to)
              .annotate(period=TruncMonth('date'))
              .values('period')
              .annotate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
              .order_by('period'))
    exp_buckets = [{'month': r['period'].strftime('%b %y'), 'amount': float(r['total'])} for r in exp_qs]

    return JsonResponse({'months': buckets, 'species': species, 'expenses': exp_buckets,
                         'range': rng, 'days': days, 'range_label': range_label})

@admin_req
def profit_loss_data(request):
    import calendar
    from django.db.models import Sum, F, ExpressionWrapper, DecimalField
    from django.db.models.functions import TruncMonth, Coalesce
    godown    = get_godown(request)
    date_from, date_to, _, _, _, _ = get_date_range(request)

    # Revenue per month — single aggregated query
    rev_qs = (SaleItem.objects
              .filter(sale__godown=godown, sale__date__gte=date_from, sale__date__lte=date_to)
              .annotate(month=TruncMonth('sale__date'))
              .values('month')
              .annotate(total=Coalesce(Sum(
                  ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
              ), 0, output_field=DecimalField()))
              .order_by('month'))
    _d = lambda x: x.date() if hasattr(x,'date') and callable(x.date) else x
    rev_map = {_d(r['month']): float(r['total']) for r in rev_qs}

    # COGS per month — items + landing expenses, both aggregated
    cogs_qs = (StockInItem.objects
               .filter(stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to)
               .annotate(month=TruncMonth('stock_in__date'))
               .values('month')
               .annotate(total=Coalesce(Sum(
                   ExpressionWrapper(F('qty_sqm') * F('rate_per_sqm'), output_field=DecimalField())
               ), 0, output_field=DecimalField()))
               .order_by('month'))
    cogs_map = {_d(r['month']): float(r['total']) for r in cogs_qs}

    landing_qs = (LandingExpense.objects
                  .filter(stock_in__godown=godown, stock_in__date__gte=date_from, stock_in__date__lte=date_to)
                  .annotate(month=TruncMonth('stock_in__date'))
                  .values('month')
                  .annotate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
                  .order_by('month'))
    for r in landing_qs:
        k = _d(r['month'])
        cogs_map[k] = cogs_map.get(k, 0) + float(r['total'])

    # Expenses per month
    exp_qs = (Expense.objects
              .filter(godown=godown, date__gte=date_from, date__lte=date_to)
              .annotate(month=TruncMonth('date'))
              .values('month')
              .annotate(total=Coalesce(Sum('amount'), 0, output_field=DecimalField()))
              .order_by('month'))
    exp_map = {_d(r['month']): float(r['total']) for r in exp_qs}

    # Build rows — one per calendar month in range
    rows = []
    d = date_from.replace(day=1)
    while d <= date_to:
        revenue = rev_map.get(d, 0)
        cogs    = cogs_map.get(d, 0)
        exp     = exp_map.get(d, 0)
        gross   = revenue - cogs
        net     = gross - exp
        rows.append({'month': d.strftime('%b %y'), 'revenue': revenue, 'cogs': cogs,
                     'gross': gross, 'expenses': exp, 'net': net})
        if d.month == 12:
            d = d.replace(year=d.year+1, month=1, day=1)
        else:
            d = d.replace(month=d.month+1, day=1)
    return JsonResponse({'pl': rows})

@admin_req
def grn_profit(request):
    godown = get_godown(request)
    grns = StockIn.objects.filter(godown=godown).select_related('supplier','po').prefetch_related(
        'items__product',
        'landing_expenses',
        'sale_items__product',
        'sale_items__sale__customer',
        'sale_items__sale',
    ).order_by('-date')
    reports = []
    for grn in grns:
        items_data=[]; grn_cost=Decimal('0'); grn_rev=Decimal('0'); grn_sold=Decimal('0'); grn_qty=Decimal('0')
        for si in grn.items.all():
            landed = si.landed_rate or si.rate_per_sqm
            sales_q = grn.sale_items.filter(product=si.product)
            qty_sold = sum(s.qty_sqm for s in sales_q)
            revenue  = sum(s.amount for s in sales_q)
            cogs     = qty_sold * landed
            profit   = revenue - cogs
            margin   = (profit/revenue*100) if revenue else Decimal('0')
            items_data.append({'product':si.product,'grn_qty':si.qty_sqm,'grn_rate':si.rate_per_sqm,'landed_rate':landed,'total_landed_cost':si.qty_sqm*landed,'qty_sold':qty_sold,'qty_remaining':si.qty_sqm-qty_sold,'revenue':revenue,'cogs':cogs,'profit':profit,'margin':round(margin,1),'status':'sold_out' if (si.qty_sqm - Decimal(str(qty_sold))) <= Decimal('0.01') else ('partial' if qty_sold>0 else 'unsold'),'sales_detail':[{'bill_number':s.sale.bill_number,'customer':s.sale.customer.name,'date':s.sale.date,'qty':s.qty_sqm,'rate':s.rate_per_sqm,'revenue':s.amount,'cogs':s.qty_sqm*landed,'profit':s.amount-(s.qty_sqm*landed)} for s in sales_q]})
            grn_cost += si.qty_sqm*landed; grn_rev += revenue; grn_sold += qty_sold; grn_qty += si.qty_sqm
        grn_p = grn_rev - sum(d['cogs'] for d in items_data)
        grn_m = (grn_p/grn_rev*100) if grn_rev else Decimal('0')
        is_fully_sold = grn_qty > 0 and (grn_qty - grn_sold) <= Decimal('0.01')
        reports.append({'grn':grn,'items':items_data,'landing_expenses':grn.landing_expenses.all(),'landing_total':grn.landing_expenses_total,'total_cost':grn_cost,'total_revenue':grn_rev,'total_qty':grn_qty,'total_sold':grn_sold,'profit':grn_p,'margin':round(grn_m,1),'pct_sold':round((float(grn_sold)/float(grn_qty)*100) if grn_qty else 0, 1),'is_fully_sold':is_fully_sold})

    # Summary stats
    fully_sold_count = sum(1 for r in reports if r['is_fully_sold'])
    total_revenue    = sum(r['total_revenue'] for r in reports)
    total_profit     = sum(r['profit'] for r in reports)
    total_qty        = sum(r['total_qty'] for r in reports)
    total_sold       = sum(r['total_sold'] for r in reports)
    overall_pct      = round(float(total_sold)/float(total_qty)*100, 1) if total_qty else 0

    return render(request, 'godown/grn_profit.html', ctx(request, {
        'active':'analytics', 'grn_reports':reports,
        'fully_sold_count': fully_sold_count,
        'total_revenue':    total_revenue,
        'total_profit':     total_profit,
        'overall_pct':      overall_pct,
    }))

@login_req
def stock_board(request):
    godown = get_godown(request)
    products = Product.objects.filter(godown=godown)
    q=request.GET.get('q','').strip(); species=request.GET.get('species','')
    thickness=request.GET.get('thickness',''); status=request.GET.get('status','')
    if q: products=products.filter(species__icontains=q)
    if species: products=products.filter(species=species)
    if thickness: products=products.filter(thickness=thickness)
    products = list(products)
    if status=='low': products=[p for p in products if p.stock_status in ('low','critical')]
    elif status=='ok': products=[p for p in products if p.stock_status=='ok']
    elif status=='out': products=[p for p in products if p.stock_status=='out']
    today  = timezone.now().date()
    cutoff = today - timedelta(days=90)
    from django.db.models import Sum, DecimalField
    from django.db.models.functions import Coalesce
    # One query for all products' 90-day sales
    sold_map_sb = {
        row['product_id']: float(row['sold'])
        for row in SaleItem.objects.filter(
            sale__godown=godown, sale__date__gte=cutoff
        ).values('product_id').annotate(
            sold=Coalesce(Sum('qty_sqm'), 0, output_field=DecimalField())
        )
    }
    for p in products:
        sold_90 = sold_map_sb.get(p.pk, 0)
        p.avg_daily = round(sold_90/90, 1)
        p.days_left = round(float(p.stock_qty)/p.avg_daily, 0) if p.avg_daily > 0 else None
    all_p = Product.objects.filter(godown=godown)
    all_products = Product.objects.filter(godown=godown)
    ok_count       = sum(1 for p in all_products if p.stock_status == 'ok')
    low_count      = sum(1 for p in all_products if p.stock_status == 'low')
    critical_count = sum(1 for p in all_products if p.stock_status == 'critical')
    out_count      = sum(1 for p in all_products if p.stock_status == 'out')
    return render(request, 'godown/stock_board.html', ctx(request, {
        'active':'stock_board','products':products,
        'species_list':all_p.values_list('species',flat=True).distinct(),
        'thickness_list':all_p.values_list('thickness',flat=True).distinct(),
        'q':q,'sel_species':species,'sel_thickness':thickness,'sel_status':status,
        'ok_count':ok_count,'low_count':low_count,
        'critical_count':critical_count,'out_count':out_count,
        'total_count':all_products.count(),
    }))

@admin_req
def reorder_alerts(request):
    from django.db.models import Sum, DecimalField
    from django.db.models.functions import Coalesce
    godown = get_godown(request)
    today  = timezone.now().date()
    cutoff = today - timedelta(days=90)

    # One query: sum sold_sqft per product in last 90 days
    sold_map = {
        row['product_id']: float(row['sold'])
        for row in SaleItem.objects.filter(
            sale__godown=godown, sale__date__gte=cutoff
        ).values('product_id').annotate(
            sold=Coalesce(Sum('qty_sqm'), 0, output_field=DecimalField())
        )
    }

    alerts = []
    for p in Product.objects.filter(godown=godown):
        sold_90 = sold_map.get(p.pk, 0)
        avg_daily = float(sold_90)/90 if sold_90 else 0
        days_left = round(float(p.stock_qty)/avg_daily) if avg_daily>0 else None
        try: alert_cfg = p.alert
        except StockAlert.DoesNotExist:
            rp = Decimal(str(round(avg_daily*14,0))) if avg_daily else p.min_stock
            alert_cfg = StockAlert.objects.create(product=p,reorder_point=max(rp,p.min_stock),reorder_qty=Decimal(str(round(avg_daily*30,0))) if avg_daily else Decimal('1000'),lead_days=14,avg_daily_sales=Decimal(str(round(avg_daily,2))))
        alert_cfg.avg_daily_sales=Decimal(str(round(avg_daily,2))); alert_cfg.save(update_fields=['avg_daily_sales'])
        urgency='ok'
        if p.stock_qty<=0: urgency='out'
        elif p.stock_qty<=alert_cfg.reorder_point:
            urgency='critical' if (days_left or 999)<=alert_cfg.lead_days else 'low'
        alerts.append({'product':p,'alert':alert_cfg,'sold_90':sold_90,'avg_daily':round(avg_daily,1),'days_left':days_left,'urgency':urgency})
    alerts.sort(key=lambda a:{'out':0,'critical':1,'low':2,'ok':3}.get(a['urgency'],4))
    return render(request, 'godown/reorder_alerts.html', ctx(request, {
        'active':'reorder_alerts','alerts':alerts,
        'critical_count':sum(1 for a in alerts if a['urgency'] in ('out','critical')),
        'low_count':sum(1 for a in alerts if a['urgency']=='low'),
    }))

@admin_req
def daily_cashflow(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    days_back = int(request.GET.get('days',30))
    rows=[]
    for i in range(days_back-1,-1,-1):
        d=today-timedelta(days=i)
        cash_in  = sum(p.amount for p in Payment.objects.filter(sale__godown=godown,date=d))
        cash_out = sum(e.amount for e in Expense.objects.filter(godown=godown,date=d,status='paid'))
        rows.append({'date':d.strftime('%d %b'),'in':float(cash_in),'out':float(cash_out),'net':float(cash_in-cash_out)})
    return JsonResponse({'rows':rows})

@admin_req
def godown_settings(request):
    godown = get_godown(request)
    if request.method == 'POST':
        for f in ['firm_name','address','phone','email','gstin','state_code',
                  'bank_name','account_no','ifsc','upi_id','invoice_prefix',
                  'po_prefix','grn_prefix','invoice_note',
                  'pan_number','cin_number','gsp_username','gsp_client_id']:
            v = request.POST.get(f)
            if v is not None: setattr(godown, f, v)
        # GST rate — validate it's a positive number
        gst_raw = request.POST.get('gst_rate', '').strip()
        if gst_raw:
            try:
                gst_val = Decimal(gst_raw)
                if gst_val >= 0:
                    godown.gst_rate = gst_val
                else:
                    messages.error(request, 'GST rate must be 0 or positive.')
            except Exception:
                messages.error(request, 'Invalid GST rate value.')
        # GSP fields
        if request.POST.get('gsp_client_secret'):
            godown.gsp_client_secret = request.POST['gsp_client_secret']
        godown.gsp_sandbox = request.POST.get('gsp_sandbox', '1') == '1'
        godown.save()
        messages.success(request, 'Settings saved.')
        return redirect('godown_settings')
    return render(request, 'godown/settings.html', ctx(request, {'active':'settings','godown_obj':godown}))


# ── PO Items API — for GRN auto-populate ─────────────────────────
@login_req
def po_items_api(request, pk):
    godown = get_godown(request)
    po = get_object_or_404(
        PurchaseOrder.objects.prefetch_related('po_items__product'),
        pk=pk, godown=godown
    )
    items = []
    for item in po.po_items.all():
        qty_ordered   = float(item.qty_sqm)
        qty_received  = float(item.qty_received)
        qty_pending   = float(item.qty_pending)

        # Skip items already fully received
        if qty_pending <= 0:
            continue

        # If original order was in pieces, recalculate pending pieces
        # from pending sqft so the GRN form shows the right piece count
        sl = float(item.sheet_length) if item.sheet_length else float(item.product.sheet_length)
        sw = float(item.sheet_width)  if item.sheet_width  else float(item.product.sheet_width)
        effective_sheet_sqm = sl * sw

        pending_pieces = None
        if item.qty_unit == 'pcs' and item.pieces and effective_sheet_sqm > 0:
            import math
            pending_pieces = math.ceil(qty_pending / effective_sheet_sqm)

        items.append({
            'product_id':      item.product.pk,
            'product_name':    str(item.product),
            'product_display': item.product.display_name,
            # Always send pending qty — not the full ordered qty
            'qty_sqm':        round(qty_pending, 4),
            'rate_per_sqm':   float(item.rate_per_sqm),
            'qty_unit':        item.qty_unit,
            'pieces':          pending_pieces,
            'sheet_length':    sl,
            'sheet_width':     sw,
            'effective_sheet_sqm':      effective_sheet_sqm,
            # Include for display/info
            'qty_ordered':     qty_ordered,
            'qty_received':    round(qty_received, 2),
            'is_partial':      qty_received > 0,
        })

    return JsonResponse({
        'po_number':   po.po_number,
        'supplier':    po.supplier.name,
        'supplier_id': po.supplier.pk,
        'po_status':   po.status,
        'items':       items,
        'all_received': len(items) == 0,
    })


# ── GST Summary API ───────────────────────────────────────────────
@admin_req
def gst_summary(request):
    """
    GST logic for face veneer godown owners:

    SALES (Output Tax):
    - You charge 12% GST on every sale invoice
    - This goes to the customer — they pay it as part of the bill
    - You collect it and owe it to the GST portal

    PURCHASES (Input Tax Credit / ITC):
    - When you buy stock from GST-registered suppliers,
      they charge you GST (12% on face veneer purchases)
    - You can offset this against what you owe to the govt

    NET GST LIABILITY = GST Collected (output) − ITC (input)
    This is what you actually pay to the GST portal each month.
    """
    godown = get_godown(request)
    today  = timezone.now().date()
    period = request.GET.get('period', 'month')

    if period == 'month':
        sales = Sale.objects.filter(godown=godown, date__month=today.month, date__year=today.year).prefetch_related('items')
        purchases = StockIn.objects.filter(godown=godown, date__month=today.month, date__year=today.year).prefetch_related('items')
    elif period == 'quarter':
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        sales = Sale.objects.filter(godown=godown, date__month__gte=q_start_month, date__year=today.year).prefetch_related('items')
        purchases = StockIn.objects.filter(godown=godown, date__month__gte=q_start_month, date__year=today.year).prefetch_related('items')
    else:  # year
        sales = Sale.objects.filter(godown=godown, date__year=today.year).prefetch_related('items')
        purchases = StockIn.objects.filter(godown=godown, date__year=today.year).prefetch_related('items')

    # OUTPUT TAX (GST collected from customers)
    taxable_sales    = sum(s.taxable_amount for s in sales)
    gst_collected    = sum(s.gst_amount for s in sales)
    cgst_collected   = sum(s.cgst for s in sales if not s.is_igst)
    sgst_collected   = sum(s.sgst for s in sales if not s.is_igst)
    igst_collected   = sum(s.igst for s in sales if s.is_igst)

    # INPUT TAX CREDIT (GST paid to suppliers — from purchase invoices)
    # Suppliers charge 12% GST on face veneer — you can claim this back
    purchase_rate  = godown.gst_rate
    taxable_purchases = sum(g.items_total for g in purchases)
    itc_available  = (taxable_purchases * purchase_rate / 100).quantize(Decimal('0.01'))
    itc_cgst       = (taxable_purchases * purchase_rate / 2 / 100).quantize(Decimal('0.01'))
    itc_sgst       = itc_cgst

    # NET PAYABLE TO GOVT
    net_liability = max(Decimal('0'), gst_collected - itc_available)

    return JsonResponse({
        'period': period,
        # Sales
        'taxable_sales': float(taxable_sales),
        'gst_collected': float(gst_collected),
        'cgst_collected': float(cgst_collected),
        'sgst_collected': float(sgst_collected),
        'igst_collected': float(igst_collected),
        # Purchases / ITC
        'taxable_purchases': float(taxable_purchases),
        'itc_available': float(itc_available),
        'itc_cgst': float(itc_cgst),
        'itc_sgst': float(itc_sgst),
        # Net
        'net_liability': float(net_liability),
        'saved_via_itc': float(itc_available),
    })


# ── Estimations ───────────────────────────────────────────────────
@login_req
def estimations(request):
    godown = get_godown(request)
    qs     = Estimation.objects.filter(godown=godown).select_related('customer').prefetch_related('est_items__product').order_by('-date','-created_at')

    if request.GET.get('format') == 'json':
        items, has_more, next_page = paginate_qs(qs, request)
        rows = []
        for e in items:
            rows.append({
                'pk': e.pk, 'est_number': e.est_number,
                'date': str(e.date), 'customer_name': e.display_name,
                'valid_until': str(e.valid_until) if e.valid_until else '',
                'days_valid': e.days_valid,
                'item_count': e.est_items.count(),
                'subtotal': float(e.subtotal),
                'gst_amount': float(e.gst_amount),
                'total': float(e.total),
                'status': e.status, 'status_display': e.get_status_display(),
                'detail_url': f'/estimations/{e.pk}/',
            })
        return JsonResponse({'rows': rows, 'has_more': has_more, 'next_page': next_page})

    first_page, has_more, _ = paginate_qs(qs, request)
    return render(request, 'godown/estimations.html', ctx(request, {
        'active': 'estimations', 'estimations': first_page, 'has_more': has_more,
    }))

@login_req
def add_estimation(request):
    godown = get_godown(request)
    from django.db.models import Sum, ExpressionWrapper, DecimalField, F
    from django.db.models.functions import Coalesce
    customers_list = Customer.objects.filter(godown=godown).annotate(
        db_total_billed=Coalesce(Sum(
            ExpressionWrapper(F('sales__items__qty_sqm') * F('sales__items__rate_per_sqm'),
                              output_field=DecimalField())
        ), 0, output_field=DecimalField()),
        db_total_received=Coalesce(
            Sum('sales__amount_received', output_field=DecimalField()),
            0, output_field=DecimalField()
        ),
    )
    for cust in customers_list:
        cust.computed_outstanding = max(Decimal("0"), cust.db_total_billed - cust.db_total_received)
    products_list = Product.objects.filter(godown=godown)
    if request.method == 'POST':
        # Validate
        errors = []
        if not request.POST.get('date'): errors.append('Please enter a date.')
        qtys  = request.POST.getlist('qty_sqm[]')
        rates = request.POST.getlist('rate_per_sqm[]')
        descs = request.POST.getlist('description[]')
        pids  = request.POST.getlist('product[]')
        line_items = []
        for i, pid in enumerate(pids):
            try:
                qty  = Decimal(qtys[i])  if i<len(qtys)  and qtys[i]  else Decimal('0')
                rate = Decimal(rates[i]) if i<len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number.'); continue
            if qty <= 0 or rate <= 0: continue
            desc = descs[i] if i < len(descs) else ''
            if pid:
                try:    product = products_list.get(pk=pid)
                except: errors.append(f'Item {i+1}: Product not found.'); continue
                label = product.display_name
            else:
                label = desc or f'Item {i+1}'
            line_items.append((pid, label, qty, rate, desc))
        if not line_items and not errors:
            errors.append('Add at least one line item.')
        if errors:
            for e in errors: messages.error(request, e)
            return render(request, 'godown/add_estimation.html', ctx(request, {
                'active':'estimations', 'customers':customers_list, 'products':products_list,
            }))

        # Preview
        if not request.POST.get('confirmed'):
            include_gst = request.POST.get('include_gst') == 'on'
            subtotal = sum(q*r for _,_,q,r,_ in line_items)
            gst_amt  = (subtotal * godown.gst_rate / 100).quantize(Decimal('0.01')) if include_gst else Decimal('0')
            total    = subtotal + gst_amt
            cust_id  = request.POST.get('customer')
            cust_name = ''
            if cust_id:
                try: cust_name = customers_list.get(pk=cust_id).name
                except: pass
            if not cust_name: cust_name = request.POST.get('customer_name','') or 'Walk-in'
            header = [
                ('Customer',    cust_name),
                ('Date',        request.POST.get('date','')),
                ('Valid Until', request.POST.get('valid_until','') or 'No expiry'),
                ('GST',         f'{godown.gst_rate}%' if include_gst else 'Not included'),
            ]
            rows = [[label, f'{q:.4f} sq.m', f'₹{r:.2f}/sq.m', f'₹{q*r:,.2f}']
                    for _, label, q, r, _ in line_items]
            totals = [('Subtotal', f'₹{subtotal:,.2f}', False)]
            if include_gst:
                totals.append((f'GST ({godown.gst_rate}%)', f'₹{gst_amt:,.2f}', False))
            totals.append(('Total', f'₹{total:,.2f}', True))
            return _preview_response(request,
                title=f'Quotation for {cust_name}', icon='📝',
                header=header, items=rows,
                item_cols=['Product / Description','Quantity','Rate','Amount'],
                totals=totals, confirm_url=request.path)

        # Confirmed — save
        with transaction.atomic():
            est_number = GodownSequence.format_number(godown, 'est')
            cust_id = request.POST.get('customer') or None
            est = Estimation.objects.create(
                godown=godown, est_number=est_number,
                customer_id=cust_id,
                customer_name=request.POST.get('customer_name',''),
                customer_phone=request.POST.get('customer_phone',''),
                date=request.POST['date'],
                valid_until=request.POST.get('valid_until') or None,
                notes=request.POST.get('notes',''),
                gst_rate=godown.gst_rate,
                include_gst=request.POST.get('include_gst') == 'on',
                status='draft',
            )
            pieces = request.POST.getlist('pieces[]')
            lens   = request.POST.getlist('sheet_length[]')
            wids   = request.POST.getlist('sheet_width[]')
            for pid, label, qty, rate, desc in line_items:
                i = pids.index(pid) if pid in pids else -1
                EstimationItem.objects.create(
                    estimation=est,
                    product_id=pid if pid else None,
                    description=desc,
                    qty_sqm=qty, rate_per_sqm=rate,
                    pieces=Decimal(pieces[i]) if i>=0 and i<len(pieces) and pieces[i] else None,
                    sheet_length=Decimal(lens[i]) if i>=0 and i<len(lens) and lens[i] else None,
                    sheet_width=Decimal(wids[i]) if i>=0 and i<len(wids) and wids[i] else None,
                )
        messages.success(request, f'Estimation {est.est_number} created.')
        return redirect('estimation_detail', pk=est.pk)
    return render(request, 'godown/add_estimation.html', ctx(request, {
        'active': 'estimations',
        'customers': customers_list,
        'products': products_list,
    }))

@login_req
def estimation_detail(request, pk):
    godown = get_godown(request)
    est = get_object_or_404(
        Estimation.objects.select_related('customer','godown').prefetch_related('est_items__product'),
        pk=pk, godown=godown
    )
    if request.method == 'POST':
        est.status = request.POST.get('status', est.status); est.save()
        messages.success(request, 'Status updated.')
        return redirect('estimation_detail', pk=pk)
    return render(request, 'godown/estimation_detail.html', ctx(request, {
        'active': 'estimations', 'est': est, 'godown_obj': godown,
    }))

@login_req
def convert_to_sale(request, pk):
    """Convert accepted estimation into a draft sale."""
    godown = get_godown(request)
    est = get_object_or_404(Estimation, pk=pk, godown=godown)
    if request.method == 'POST':
        # Need a real Customer record to create a sale
        customer = est.customer
        if not customer:
            # Walk-in estimation — create a customer record first
            name = est.customer_name or 'Walk-in Customer'
            customer = Customer.objects.create(
                godown=godown, name=name,
                phone=est.customer_phone or '',
            )
            est.customer = customer
            est.save(update_fields=['customer'])

        # Validate at least one item has a product
        items = [i for i in est.est_items.all() if i.product and i.qty_sqm > 0 and i.rate_per_sqm > 0]
        if not items:
            messages.error(request, 'Estimation has no valid items to convert. Add products with qty and rate.')
            return redirect('estimation_detail', pk=pk)

        with transaction.atomic():
            bill_number = GodownSequence.format_number(godown, 'sale')
            sale = Sale.objects.create(
                godown=godown, bill_number=bill_number,
                customer=customer,
                date=timezone.now().date(),
                amount_received=0,
                payment_mode='credit',
                gst_rate=est.gst_rate,
            )
            for item in items:
                SaleItem.objects.create(
                    sale=sale, product=item.product,
                    qty_sqm=item.qty_sqm, rate_per_sqm=item.rate_per_sqm,
                    cost_at_sale=item.product.avg_cost,
                )
            est.status = 'accepted'; est.save()
        messages.success(request, f'Estimation converted to Sale {sale.bill_number}.')
        return redirect('invoice', pk=sale.pk)
    return render(request, 'godown/estimation_detail.html', ctx(request, {
        'active': 'estimations', 'est': est, 'godown_obj': godown, 'confirm_convert': True,
    }))


# Import missing models at top


# ── GST Report Page ───────────────────────────────────────────────
@admin_req
def gst_report(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    # Build month-by-month GST data for last 12 months
    months = []
    for i in range(11, -1, -1):
        d = (today.replace(day=1) - timedelta(days=i*28)).replace(day=1)
        m, y = d.month, d.year
        sales = list(Sale.objects.filter(godown=godown, date__month=m, date__year=y).prefetch_related('items'))
        purchases = list(StockIn.objects.filter(godown=godown, date__month=m, date__year=y).prefetch_related('items'))
        taxable_sales = sum(s.taxable_amount for s in sales)
        gst_collected = sum(s.gst_amount for s in sales)
        purchase_val  = sum(g.items_total for g in purchases)
        itc           = (purchase_val * godown.gst_rate / 100).quantize(Decimal('0.01'))
        net_payable   = max(Decimal('0'), gst_collected - itc)
        months.append({
            'month': d.strftime('%b %Y'), 'taxable_sales': taxable_sales,
            'gst_collected': gst_collected, 'purchase_val': purchase_val,
            'itc': itc, 'net_payable': net_payable,
        })
    return render(request, 'godown/gst_report.html', ctx(request, {
        'active': 'analytics', 'months': months, 'gst_rate': godown.gst_rate,
    }))


# ── Lookup table views ────────────────────────────────────────────


@admin_req
def lookup_list(request):
    godown = get_godown(request)
    # Group by category
    grouped = {}
    for cat in LookupCategory:
        grouped[cat] = LookupValue.objects.filter(godown=godown, category=cat).order_by('sort_order', 'label')
    return render(request, 'godown/lookup_list.html', ctx(request, {
        'active': 'settings',
        'grouped': grouped,
        'categories': LookupCategory,
    }))


@admin_req
def lookup_add(request):
    godown = get_godown(request)
    if request.method == 'POST':
        category   = request.POST.get('category')
        value      = request.POST.get('value', '').strip()
        label      = request.POST.get('label', '').strip()
        sort_order = int(request.POST.get('sort_order', 0) or 0)
        is_default = request.POST.get('is_default') == 'on'
        is_active  = request.POST.get('is_active', 'on') == 'on'

        if not value or not label or not category:
            messages.error(request, 'Category, value and label are required.')
        elif LookupValue.objects.filter(godown=godown, category=category, value=value).exists():
            messages.error(request, f'Value "{value}" already exists in this category.')
        else:
            # If setting as default, unset others
            if is_default:
                LookupValue.objects.filter(godown=godown, category=category, is_default=True).update(is_default=False)
            LookupValue.objects.create(
                godown=godown, category=category, value=value, label=label,
                sort_order=sort_order, is_default=is_default, is_active=is_active,
            )
            messages.success(request, f'"{label}" added to {category}.')
            return redirect('lookup_list')
    return render(request, 'godown/lookup_form.html', ctx(request, {
        'active': 'settings',
        'categories': LookupCategory,
        'title': 'Add Lookup Value',
    }))


@admin_req
def lookup_edit(request, pk):
    godown = get_godown(request)
    lv = get_object_or_404(LookupValue, pk=pk, godown=godown)
    if request.method == 'POST':
        lv.label      = request.POST.get('label', lv.label).strip()
        lv.sort_order = int(request.POST.get('sort_order', lv.sort_order) or 0)
        is_default    = request.POST.get('is_default') == 'on'
        lv.is_active  = request.POST.get('is_active', 'on') == 'on'
        if is_default and not lv.is_default:
            LookupValue.objects.filter(godown=godown, category=lv.category, is_default=True).update(is_default=False)
        lv.is_default = is_default
        lv.save()
        messages.success(request, f'"{lv.label}" updated.')
        return redirect('lookup_list')
    return render(request, 'godown/lookup_form.html', ctx(request, {
        'active': 'settings',
        'lv': lv,
        'categories': LookupCategory,
        'title': f'Edit — {lv.label}',
    }))


@admin_req
def lookup_toggle(request, pk):
    """Toggle active/inactive without deleting."""
    godown = get_godown(request)
    lv = get_object_or_404(LookupValue, pk=pk, godown=godown)
    lv.is_active = not lv.is_active
    lv.save(update_fields=['is_active'])
    state = 'activated' if lv.is_active else 'deactivated'
    messages.success(request, f'"{lv.label}" {state}.')
    return redirect('lookup_list')


@admin_req
def lookup_delete(request, pk):
    godown = get_godown(request)
    lv = get_object_or_404(LookupValue, pk=pk, godown=godown)
    if request.method == 'POST':
        label = lv.label
        lv.delete()
        messages.success(request, f'"{label}" deleted.')
    return redirect('lookup_list')


@login_req
def lookup_api(request, category):
    """Return active lookup values as JSON for dynamic form population."""
    godown = get_godown(request)
    values = LookupValue.for_godown(godown, category)
    return JsonResponse({
        'category': category,
        'values': [
            {'value': v.value, 'label': v.label,
             'is_default': v.is_default, 'sort_order': v.sort_order}
            for v in values
        ]
    })


# ── GRN Detail ────────────────────────────────────────────────────

@login_req
@login_req
def edit_grn(request, pk):
    godown = get_godown(request)
    grn = get_object_or_404(StockIn.objects.prefetch_related('items__product','landing_expenses'), pk=pk, godown=godown)
    suppliers_list = Supplier.objects.filter(godown=godown, supplier_type__in=['material','both'])
    products_list  = Product.objects.filter(godown=godown)
    service_vendors = Supplier.objects.filter(godown=godown, supplier_type__in=['service','both'])

    if request.method == 'POST':
        errors = []
        supplier_id = request.POST.get('supplier', '').strip()
        date_str    = request.POST.get('date', '').strip()
        if not supplier_id: errors.append('Please select a supplier.')
        if not date_str:    errors.append('Please enter a date.')

        qtys  = request.POST.getlist('qty_sqm[]')
        rates = request.POST.getlist('rate_per_sqm[]')
        pids  = request.POST.getlist('product[]')
        units = request.POST.getlist('qty_unit[]')
        line_items = []
        for i, pid in enumerate(pids):
            if not pid: continue
            try:
                qty_str = qtys[i] if i < len(qtys) else ''
                qty  = Decimal(qty_str)  if qty_str  else Decimal('0')
                rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number.'); continue
            if qty <= 0:  errors.append(f'Item {i+1}: Quantity must be > 0.'); continue
            if rate <= 0: errors.append(f'Item {i+1}: Rate must be > 0.');     continue
            try:    product = products_list.get(pk=pid)
            except: errors.append(f'Item {i+1}: Product not found.'); continue
            unit = units[i] if i < len(units) else 'sqm'
            line_items.append((product, qty, rate, unit))

        if not line_items and not errors:
            errors.append('Add at least one item with product, quantity and rate.')

        # GRN edit: check we are not reducing below what has already been sold
        if not errors:
            for product, qty, rate, unit in line_items:
                # qty_sold from this specific GRN (FIFO — product may have been sold across GRNs)
                sold_from_grn = sum(
                    si.qty_sqm for si in SaleItem.objects.filter(
                        product=product, grn_source=grn
                    )
                ) if hasattr(SaleItem, 'grn_source') else Decimal('0')
                if qty < sold_from_grn:
                    errors.append(
                        f'{product.display_name}: Cannot reduce to {qty:.4f} sq.m — '
                        f'{sold_from_grn:.4f} sq.m has already been sold from this GRN.'
                    )

        # Block edit if any landing expense already has a payment recorded —
        # editing would silently delete that payment history.
        paid_expenses = grn.landing_expenses.filter(amount_paid__gt=0)
        if paid_expenses.exists():
            names = ', '.join(
                f'{e.vendor.name if e.vendor else e.paid_to or "Unknown vendor"} (₹{e.amount_paid:,.0f} paid)'
                for e in paid_expenses
            )
            errors.append(
                f'Cannot edit items/expenses — payment already recorded against: {names}. '
                f'Settle those vendor payments first before editing this GRN.'
            )

        if errors:
            for err in errors: messages.error(request, err)
            return render(request, 'godown/edit_grn.html', ctx(request, {
                'active': 'stock_in', 'grn': grn,
                'suppliers': suppliers_list, 'products': products_list,
                'service_vendors': service_vendors,
                'form_errors': errors,
            }))

        with transaction.atomic():
            grn.supplier_id    = supplier_id
            grn.date           = date_str
            grn.invoice_number = request.POST.get('invoice_number', '')
            grn.notes          = request.POST.get('notes', '')
            # Step 1: reverse old stock additions (GRN added stock, so remove it)
            for old in grn.items.all():
                Product.objects.filter(pk=old.product_id).update(
                    stock_qty=DbF('stock_qty') - old.qty_sqm
                )
            grn.items.all().delete()
            grn.landing_expenses.all().delete()
            # Step 2: write new items and add stock
            si_items = []
            for product, qty, rate, unit in line_items:
                # Reload product to get fresh stock_qty after restore
                product = Product.objects.get(pk=product.pk)
                product.update_avg_cost(qty, rate)
                si = StockInItem.objects.create(
                    stock_in=grn, product=product,
                    qty_sqm=qty, rate_per_sqm=rate, landed_rate=rate, qty_unit=unit,
                )
                Product.objects.filter(pk=product.pk).update(
                    stock_qty=DbF('stock_qty') + qty
                )
                si_items.append(si)
            # Landing expenses
            exp_cats    = request.POST.getlist('exp_cat[]')
            exp_amts    = request.POST.getlist('exp_amt[]')
            exp_paids   = request.POST.getlist('exp_paid_to[]')
            exp_descs   = request.POST.getlist('exp_desc[]')
            exp_vendors = request.POST.getlist('exp_vendor[]')
            total_landing = Decimal('0')
            for j, cat in enumerate(exp_cats):
                amt_str = exp_amts[j] if j < len(exp_amts) else ''
                if not cat or not amt_str: continue
                try: amt = Decimal(amt_str)
                except: continue
                if amt <= 0: continue
                vendor_id = exp_vendors[j] if j < len(exp_vendors) else ''
                LandingExpense.objects.create(
                    stock_in=grn, category=cat, amount=amt,
                    paid_to=exp_paids[j] if j < len(exp_paids) else '',
                    description=exp_descs[j] if j < len(exp_descs) else '',
                    vendor_id=vendor_id if vendor_id else None,
                )
                total_landing += amt
            if si_items and total_landing > 0:
                total_qty = sum(s.qty_sqm for s in si_items)
                for s in si_items:
                    share = (s.qty_sqm / total_qty) * total_landing
                    s.landed_rate = s.rate_per_sqm + (share / s.qty_sqm)
                    s.save(update_fields=['landed_rate'])
            grn.save()
        messages.success(request, f'GRN {grn.grn_number} updated successfully.')
        return redirect('grn_detail', pk=pk)

    return render(request, 'godown/edit_grn.html', ctx(request, {
        'active': 'stock_in', 'grn': grn,
        'suppliers': suppliers_list, 'products': products_list,
        'service_vendors': service_vendors,
    }))


@login_req
def grn_detail(request, pk):
    godown = get_godown(request)
    grn = get_object_or_404(
        StockIn.objects.select_related('supplier', 'po')
               .prefetch_related('items__product', 'landing_expenses'),
        pk=pk, godown=godown
    )
    return render(request, 'godown/grn_detail.html', ctx(request, {
        'active': 'stock_in', 'grn': grn,
    }))


# ── Sale Detail ───────────────────────────────────────────────────

@login_req
@login_req
def edit_sale(request, pk):
    godown = get_godown(request)
    sale = get_object_or_404(Sale.objects.prefetch_related('items__product'), pk=pk, godown=godown)
    customers_list = Customer.objects.filter(godown=godown)
    products_list  = Product.objects.filter(godown=godown)

    if request.method == 'POST':
        errors = []
        customer_id = request.POST.get('customer', '').strip()
        date_str    = request.POST.get('date', '').strip()
        if not customer_id: errors.append('Please select a customer.')
        if not date_str:    errors.append('Please enter a date.')

        qtys = request.POST.getlist('qty_sqm[]')
        rates = request.POST.getlist('rate_per_sqm[]')
        pids  = request.POST.getlist('product[]')
        line_items = []
        for i, pid in enumerate(pids):
            if not pid: continue
            try:
                qty  = Decimal(qtys[i])  if i < len(qtys)  and qtys[i]  else Decimal('0')
                rate = Decimal(rates[i]) if i < len(rates) and rates[i] else Decimal('0')
            except Exception:
                errors.append(f'Item {i+1}: Invalid number — enter digits only.'); continue
            if qty <= 0:  errors.append(f'Item {i+1}: Quantity must be > 0.'); continue
            if rate <= 0: errors.append(f'Item {i+1}: Rate must be > 0.'); continue
            try:    product = products_list.get(pk=pid)
            except: errors.append(f'Item {i+1}: Product not found.'); continue
            line_items.append((product, qty, rate))

        if not line_items and not errors:
            errors.append('Add at least one item with product, quantity and rate.')

        # Stock availability — available = current stock + what this sale holds
        if not errors:
            old_qtys = {}
            for old in sale.items.all():
                old_qtys[old.product_id] = old_qtys.get(old.product_id, Decimal('0')) + old.qty_sqm
            new_qtys = {}
            for p, q, r in line_items:
                new_qtys[p.pk] = new_qtys.get(p.pk, Decimal('0')) + q
            for product, qty, rate in line_items:
                available = product.stock_qty + old_qtys.get(product.pk, Decimal('0'))
                if new_qtys[product.pk] > available:
                    errors.append(
                        f'{product.display_name}: Only {available:.4f} sq.m available '
                        f'(current stock {product.stock_qty:.4f} + {old_qtys.get(product.pk,0):.4f} on this sale), '
                        f'but entered {new_qtys[product.pk]:.4f} sq.m.'
                    )

        if errors:
            for err in errors: messages.error(request, err)
            return render(request, 'godown/edit_sale.html', ctx(request, {
                'active': 'sales', 'sale': sale,
                'customers': customers_list, 'products': products_list,
                'godown_obj': godown, 'form_errors': errors,
            }))

        with transaction.atomic():
            sale.customer_id      = customer_id
            sale.date             = date_str
            sale.due_date         = request.POST.get('due_date') or None
            sale.transport        = request.POST.get('transport', '')
            sale.po_reference     = request.POST.get('po_reference', '')
            sale.vehicle_number   = request.POST.get('vehicle_number', '')
            sale.transporter_name = request.POST.get('transporter_name', '')
            sale.transporter_id   = request.POST.get('transporter_id', '')
            sale.transport_distance = request.POST.get('transport_distance') or None
            sale.transport_mode   = request.POST.get('transport_mode', '1')
            sale.ship_name    = request.POST.get('ship_name', '')
            sale.ship_addr1   = request.POST.get('ship_addr1', '')
            sale.ship_pincode = request.POST.get('ship_pincode', '')
            sale.ship_state   = request.POST.get('ship_state', '')
            sale.notes        = request.POST.get('notes', '')
            # Restore stock from OLD items atomically
            for old in sale.items.all():
                Product.objects.filter(pk=old.product_id).update(
                    stock_qty=DbF('stock_qty') + old.qty_sqm
                )
            sale.items.all().delete()
            # Write NEW items atomically
            for product, qty, rate in line_items:
                product_fresh = Product.objects.get(pk=product.pk)
                SaleItem.objects.create(sale=sale, product=product_fresh,
                    qty_sqm=qty, rate_per_sqm=rate, cost_at_sale=product_fresh.avg_cost)
                Product.objects.filter(pk=product.pk).update(
                    stock_qty=DbF('stock_qty') - qty
                )
            sale.save()
        messages.success(request, f'Sale {sale.bill_number} updated successfully.')
        return redirect('sale_detail', pk=pk)

    return render(request, 'godown/edit_sale.html', ctx(request, {
        'active': 'sales', 'sale': sale,
        'customers': customers_list, 'products': products_list,
        'godown_obj': godown,
    }))


@login_req
def sale_detail(request, pk):
    godown = get_godown(request)
    sale = get_object_or_404(
        Sale.objects.select_related('customer')
            .prefetch_related('items__product', 'payments'),
        pk=pk, godown=godown
    )
    return render(request, 'godown/sale_detail.html', ctx(request, {
        'active': 'sales', 'sale': sale, 'godown_obj': godown,
    }))


# ── Delete master data (only if unused) ──────────────────────────
@admin_req
def delete_customer(request, pk):
    godown = get_godown(request)
    c = get_object_or_404(Customer, pk=pk, godown=godown)
    if Sale.objects.filter(customer=c, godown=godown).exists():
        messages.error(request, f'Cannot delete "{c.name}" — they have sales records.')
    elif request.method == 'POST':
        name = c.name; c.delete()
        messages.success(request, f'Customer "{name}" deleted.')
    return redirect('customers')

@admin_req
def delete_supplier(request, pk):
    godown = get_godown(request)
    s = get_object_or_404(Supplier, pk=pk, godown=godown)
    has_purchase_records = StockIn.objects.filter(supplier=s, godown=godown).exists() or \
                            PurchaseOrder.objects.filter(supplier=s, godown=godown).exists()
    has_service_records  = LandingExpense.objects.filter(vendor=s, stock_in__godown=godown).exists()
    if has_purchase_records or has_service_records:
        reason = []
        if has_purchase_records: reason.append('purchase records')
        if has_service_records:  reason.append('service charge records')
        messages.error(request, f'Cannot delete "{s.name}" — they have {" and ".join(reason)}.')
    elif request.method == 'POST':
        name = s.name; s.delete()
        messages.success(request, f'Supplier "{name}" deleted.')
    return redirect('suppliers')

@admin_req
def delete_product(request, pk):
    godown = get_godown(request)
    p = get_object_or_404(Product, pk=pk, godown=godown)
    if SaleItem.objects.filter(product=p, sale__godown=godown).exists() or StockInItem.objects.filter(product=p, stock_in__godown=godown).exists():
        messages.error(request, f'Cannot delete "{p.display_name}" — it has stock/sale records.')
    elif request.method == 'POST':
        name = p.display_name; p.delete()
        messages.success(request, f'Product "{name}" deleted.')
    return redirect('products')


# ── Supplier History / Statement ──────────────────────────────────
@login_req
def supplier_history(request, pk):
    godown   = get_godown(request)
    supplier = get_object_or_404(Supplier, pk=pk, godown=godown)

    pos  = PurchaseOrder.objects.filter(supplier=supplier, godown=godown)\
               .prefetch_related('po_items__product').order_by('date')
    grns = StockIn.objects.filter(supplier=supplier, godown=godown)\
               .prefetch_related('items__product','landing_expenses').order_by('date')
    # Landing expenses where THIS supplier is the service vendor (may be a
    # different GRN's supplier, or the same one if supplier_type='both')
    vendor_expenses = LandingExpense.objects.filter(
        vendor=supplier, stock_in__godown=godown
    ).select_related('stock_in').prefetch_related('payments').order_by('stock_in__date')

    # Build chronological ledger
    # Each row: date, type, ref, debit (what we owe), credit (what we paid), balance
    events = []

    for po in pos:
        if po.advance_paid > 0:
            events.append({
                'date':    po.date,
                'sort':    po.date,
                'type':    'advance',
                'ref':     po.po_number,
                'note':    f'Advance on PO — {po.get_status_display()}',
                'debit':   Decimal('0'),
                'credit':  po.advance_paid_inr,
                'obj':     po,
                'currency': po.currency,
                'foreign_amt': po.advance_paid if po.currency == 'USD' else None,
                'exchange_rate': po.advance_exchange_rate if po.currency == 'USD' else None,
            })

    for grn in grns:
        # GRN arrival = we owe money for MATERIAL ONLY (debit) — landing expenses
        # are tracked separately against their own service vendor, never here.
        if grn.items_total > 0:
            events.append({
                'date':   grn.date,
                'sort':   grn.date,
                'type':   'grn',
                'ref':    grn.grn_number,
                'note':   f'{grn.items.count()} item(s) · {grn.get_payment_mode_display()} (material only)',
                'debit':  grn.items_total,
                'credit': Decimal('0'),
                'obj':    grn,
                'currency': grn.payment_currency,
                'foreign_amt': None,
                'exchange_rate': None,
            })
        # GRN payment = we paid (credit) — payment against material cost
        if grn.amount_paid_inr > 0:
            events.append({
                'date':   grn.date,
                'sort':   grn.date,
                'type':   'payment',
                'ref':    grn.grn_number,
                'note':   f'Payment on {grn.grn_number} — {grn.get_payment_mode_display()}',
                'debit':  Decimal('0'),
                'credit': grn.amount_paid_inr,
                'obj':    grn,
                'currency': grn.payment_currency,
                'foreign_amt': grn.amount_paid if grn.payment_currency == 'USD' else None,
                'exchange_rate': grn.exchange_rate if grn.payment_currency == 'USD' else None,
            })

    # Service charges where this supplier is the vendor (transport, forklift etc.)
    for exp in vendor_expenses:
        events.append({
            'date':   exp.stock_in.date,
            'sort':   exp.stock_in.date,
            'type':   'service',
            'ref':    exp.stock_in.grn_number,
            'note':   f'{exp.get_category_display()}{" — " + exp.description if exp.description else ""}',
            'debit':  exp.amount,
            'credit': Decimal('0'),
            'obj':    exp,
            'grn_pk': exp.stock_in.pk,
            'currency': 'INR',
            'foreign_amt': None,
            'exchange_rate': None,
        })
        # Each individual payment gets its own ledger row — not collapsed into one total
        for vp in exp.payments.all():
            events.append({
                'date':   vp.date,
                'sort':   vp.date,
                'type':   'service_payment',
                'ref':    exp.stock_in.grn_number,
                'note':   f'Payment for {exp.get_category_display()} — {exp.stock_in.grn_number}'
                          f'{" · " + vp.get_payment_mode_display() if vp.payment_mode else ""}'
                          f'{" · Ref: " + vp.reference if vp.reference else ""}',
                'debit':  Decimal('0'),
                'credit': vp.amount,
                'obj':    vp,
                'grn_pk': exp.stock_in.pk,
                'currency': 'INR',
                'foreign_amt': None,
                'exchange_rate': None,
            })

    # Sort by date then type (grn/service before payment on same day)
    type_order = {'advance': 0, 'grn': 1, 'service': 1, 'payment': 2, 'service_payment': 2}
    events.sort(key=lambda e: (e['sort'], type_order.get(e['type'], 9)))

    # Compute running balance
    running = Decimal('0')
    for ev in events:
        running += ev['debit'] - ev['credit']
        ev['balance'] = running

    # Summary — material cost and service charges tracked separately, combined for this supplier's total view
    total_material_purchased = sum(g.items_total for g in grns)
    total_material_paid_inr  = sum(g.amount_paid_inr for g in grns)
    total_advance            = sum(p.advance_paid_inr for p in pos)
    total_service_billed     = sum(e.amount for e in vendor_expenses)
    total_service_paid       = sum(e.amount_paid for e in vendor_expenses)

    total_purchased = total_material_purchased + total_service_billed
    total_credit    = total_material_paid_inr + total_advance + total_service_paid
    outstanding     = total_purchased - total_credit
    # positive = we owe, negative = they owe us (advance credit)

    # Species breakdown (material purchases only)
    from collections import defaultdict
    species_map = defaultdict(lambda: {'qty': Decimal('0'), 'amount': Decimal('0')})
    for grn in grns:
        for item in grn.items.all():
            species_map[item.product.species]['qty']    += item.qty_sqm
            species_map[item.product.species]['amount'] += item.amount

    return render(request, 'godown/supplier_history.html', ctx(request, {
        'active':          'suppliers',
        'supplier':        supplier,
        'events':          events,
        'pos':             pos,
        'grns':            grns,
        'vendor_expenses': vendor_expenses,
        'total_purchased': total_purchased,
        'total_material_purchased': total_material_purchased,
        'total_service_billed':     total_service_billed,
        'total_paid_inr':  total_material_paid_inr,
        'total_advance':   total_advance,
        'total_credit':    total_credit,
        'outstanding':     outstanding,
        'species_map':     dict(species_map),
        'grn_count':       grns.count(),
        'po_count':        pos.count(),
        'service_count':   vendor_expenses.count(),
    }))


# ── Stock Damage ──────────────────────────────────────────────────
@login_req
def damage_list(request):
    godown  = get_godown(request)
    damages = StockDamage.objects.filter(godown=godown)\
                .select_related('product', 'grn__supplier', 'reported_by')\
                .order_by('-date', '-created_at')

    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    from django.db.models import DecimalField as DF, ExpressionWrapper as EW, F

    # Summary stats
    total_qty        = damages.aggregate(t=Coalesce(Sum('qty_sqm'), 0, output_field=DF()))['t']
    # Write-off value = sum(qty * cost_rate)
    total_write_off  = sum(d.write_off_value for d in damages)

    # By category
    from collections import defaultdict
    by_category = defaultdict(lambda: {'qty': Decimal('0'), 'value': Decimal('0'), 'count': 0})
    for d in damages:
        by_category[d.get_category_display()]['qty']   += d.qty_sqm
        by_category[d.get_category_display()]['value'] += d.write_off_value
        by_category[d.get_category_display()]['count'] += 1

    return render(request, 'godown/damage_list.html', ctx(request, {
        'active':          'damages',
        'damages':         damages,
        'total_qty':       total_qty,
        'total_write_off': total_write_off,
        'by_category':     dict(by_category),
    }))


@login_req
def add_damage(request, grn_pk=None):
    godown   = get_godown(request)
    products = Product.objects.filter(godown=godown)
    grns     = StockIn.objects.filter(godown=godown)\
                  .select_related('supplier').order_by('-date')
    pre_grn  = get_object_or_404(StockIn, pk=grn_pk, godown=godown) if grn_pk else None

    if request.method == 'POST':
        errors = []
        product_id = request.POST.get('product')
        qty        = request.POST.get('qty_sqft', '').strip()
        category   = request.POST.get('category', 'other')
        grn_id     = request.POST.get('grn') or None
        date       = request.POST.get('date') or timezone.now().date()
        description = request.POST.get('description', '').strip()

        if not product_id:
            errors.append('Select a product.')
        if not qty or Decimal(qty) <= 0:
            errors.append('Enter a valid quantity.')

        if not errors:
            product = get_object_or_404(Product, pk=product_id, godown=godown)
            grn_obj = get_object_or_404(StockIn, pk=grn_id, godown=godown) if grn_id else None

            # Check available stock in sqft (DB unit); qty entered by user is sq.m
            total_received = sum(
                i.qty_sqm for i in StockInItem.objects.filter(product=product, stock_in__godown=godown)
            )
            total_sold = sum(
                i.qty_sqm for i in SaleItem.objects.filter(product=product, sale__godown=godown)
            )
            total_damaged_existing = sum(
                d.qty_sqm for d in StockDamage.objects.filter(product=product, godown=godown)
            )
            available = total_received - total_sold - total_damaged_existing
            qty_dec   = Decimal(qty)

            if qty_dec > available:
                errors.append(
                    f'Only {available:.4f} sq.m available for {product.display_name} '
                    f'(received {total_received:.4f} − sold {total_sold:.4f} − '
                    f'already damaged {total_damaged_existing:.4f} sq.m).'
                )

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # Use landed_rate if from a specific GRN batch, else product avg_cost
            if grn_obj:
                si_item = StockInItem.objects.filter(
                    stock_in=grn_obj, product=product
                ).first()
                cost_rate = si_item.landed_rate if si_item else product.avg_cost
            else:
                cost_rate = product.avg_cost

            write_off = (qty_dec * cost_rate).quantize(Decimal('0.01'))

            # Preview before recording damage (irreversible stock deduction)
            if not request.POST.get('confirmed'):
                header = [
                    ('Product',    product.display_name),
                    ('Quantity',   f'{qty_dec:.4f} sq.m'),
                    ('Category',   dict(StockDamage.DAMAGE_CATEGORY).get(category, category)),
                    ('Date',       str(date)),
                    ('Cost Rate',  f'₹{cost_rate:.2f}/sq.m'),
                    ('Write-off',  f'₹{write_off:,.2f}'),
                ]
                if grn_obj:
                    header.insert(1, ('GRN', grn_obj.grn_number))
                if description:
                    header.append(('Note', description))
                return _preview_response(request,
                    title=f'Record Damage — {product.display_name}', icon='⚠️',
                    header=header, items=[],
                    item_cols=[],
                    totals=[('Write-off Value', f'₹{write_off:,.2f}', True)],
                    confirm_url=request.path,
                    warning=f'This will permanently deduct {qty_dec:.4f} sq.m from stock. This action cannot be undone.')

            damage = StockDamage.objects.create(
                godown=godown, product=product, grn=grn_obj,
                date=date, category=category,
                qty_sqm=qty_dec, cost_rate=cost_rate,
                description=description, reported_by=request.user,
            )

            # Deduct from product stock_qty
            product.stock_qty -= qty_dec
            product.save(update_fields=['stock_qty'])

            messages.success(
                request,
                f'Damage recorded: {qty_dec:.2f} sq.m {product.display_name} '
                f'({damage.get_category_display()}) — '
                f'write-off value ₹{damage.write_off_value:,.0f}.'
            )
            return redirect('damage_list')

    return render(request, 'godown/add_damage.html', ctx(request, {
        'active':    'damages',
        'products':  products,
        'grns':      grns,
        'pre_grn':   pre_grn,
        'categories': StockDamage.DAMAGE_CATEGORY,
    }))


@login_req
def delete_damage(request, pk):
    godown = get_godown(request)
    damage = get_object_or_404(StockDamage, pk=pk, godown=godown)
    if request.method == 'POST':
        # Restore stock qty
        product = damage.product
        product.stock_qty += damage.qty_sqm
        product.save(update_fields=['stock_qty'])
        damage.delete()
        messages.success(request, 'Damage record deleted and stock restored.')
    return redirect('damage_list')


# ── e-Invoice JSON (GSP format) ───────────────────────────────────
@login_req
def einvoice_json(request, pk):
    """
    Generate e-Invoice payload in NIC/GSP JSON format.
    Ready to POST to GSP API when integrated.
    Reference: https://einvoice1.gst.gov.in/Documents/EINV-API-Spec.pdf
    """
    godown = get_godown(request)
    sale   = get_object_or_404(
        Sale.objects.select_related('customer', 'godown')
            .prefetch_related('items__product'),
        pk=pk, godown=godown
    )

    if sale.sale_type == 'cash_memo':
        return JsonResponse({'error': 'Cash Memo transactions are not eligible for e-Invoice.'}, status=400)

    items = list(sale.items.all())
    item_list = []
    for i, item in enumerate(items, 1):
        taxable = float(item.qty_sqm * item.rate_per_sqm)
        cgst_rate = float(sale.gst_rate) / 2 if not sale.is_igst else 0
        igst_rate = float(sale.gst_rate) if sale.is_igst else 0
        gst_amt   = taxable * float(sale.gst_rate) / 100
        item_list.append({
            "SlNo":       str(i),
            "PrdDesc":    item.product.display_name,
            "IsServc":    "N",
            "HsnCd":      item.product.hsn_code or "4408",
            "Barcde":     "",
            "Qty":        float(item.qty_sqm),
            "FreeQty":    0,
            "Unit":       item.product.uom or "SQF",
            "UnitPrice":  float(item.rate_per_sqm),
            "TotAmt":     taxable,
            "Discount":   0,
            "PreTaxVal":  taxable,
            "AssAmt":     taxable,
            "GstRt":      float(sale.gst_rate),
            "IgstAmt":    round(gst_amt, 2) if sale.is_igst else 0,
            "CgstAmt":    round(gst_amt / 2, 2) if not sale.is_igst else 0,
            "SgstAmt":    round(gst_amt / 2, 2) if not sale.is_igst else 0,
            "CesRt":      0,
            "CesAmt":     0,
            "CesNonAdvlAmt": 0,
            "StateCesRt": 0,
            "StateCesAmt": 0,
            "StateCesNonAdvlAmt": 0,
            "OthChrg":    0,
            "TotItemVal": round(taxable + gst_amt, 2),
        })

    taxable_total = float(sale.total_amount)
    gst_total     = float(sale.gst_amount)
    grand_total   = float(sale.grand_total)

    payload = {
        "Version": "1.1",
        "TranDtls": {
            "TaxSch":  "GST",
            "SupTyp":  "B2B" if sale.customer.gstin else "B2C",
            "RegRev":  "N",
            "EcmGstin": None,
            "IgstOnIntra": "N"
        },
        "DocDtls": {
            "Typ":   "INV",
            "No":    sale.bill_number,
            "Dt":    sale.date.strftime('%d/%m/%Y'),
        },
        "SellerDtls": {
            "Gstin": godown.gstin or "",
            "LglNm": godown.firm_name,
            "TrdNm": godown.firm_name,
            "Addr1": (godown.address or "").split(',')[0][:100],
            "Addr2": "",
            "Loc":   "Kerala",
            "Pin":   int(godown.gstin[2:7]) if godown.gstin and len(godown.gstin) >= 7 and godown.gstin[2:7].isdigit() else 0,
            "Stcd":  godown.state_code or "32",
            "Ph":    godown.phone or "",
            "Em":    godown.email or "",
        },
        "BuyerDtls": {
            "Gstin": sale.customer.gstin or sale.customer.gst_number or "URP",
            "LglNm": sale.customer.name,
            "TrdNm": sale.customer.name,
            "Pos":   sale.customer.place_of_supply or sale.customer.state_code or godown.state_code or "32",
            "Addr1": sale.customer.location or "",
            "Addr2": "",
            "Loc":   sale.customer.location or "",
            "Pin":   int(sale.customer.pincode) if sale.customer.pincode and sale.customer.pincode.isdigit() else 0,
            "Stcd":  sale.customer.state_code or "32",
            "Ph":    sale.customer.phone or "",
            "Em":    getattr(sale.customer, "email", "") or "",
        },
        "DispDtls": {
            "Nm":   sale.dispatch_name or godown.firm_name,
            "Addr1": sale.dispatch_addr1 or (godown.address or "")[:100],
            "Addr2": "",
            "Loc":  "Kerala",
            "Pin":  int(sale.dispatch_pincode) if sale.dispatch_pincode and sale.dispatch_pincode.isdigit() else 0,
            "Stcd": sale.dispatch_state or godown.state_code or "32",
        },
        "ShipDtls": {
            "Gstin": sale.customer.gstin or sale.customer.gst_number or "URP",
            "LglNm": sale.ship_name or sale.customer.name,
            "TrdNm": sale.ship_name or sale.customer.name,
            "Addr1": sale.ship_addr1 or sale.customer.location or "",
            "Addr2": "",
            "Loc":   sale.customer.location or "",
            "Pin":   int(sale.ship_pincode or sale.customer.pincode or 0) if (sale.ship_pincode or sale.customer.pincode or '').isdigit() else 0,
            "Stcd":  sale.ship_state or sale.customer.state_code or "32",
        },
        "ItemList": item_list,
        "ValDtls": {
            "AssVal":   round(taxable_total, 2),
            "CgstVal":  round(gst_total / 2, 2) if not sale.is_igst else 0,
            "SgstVal":  round(gst_total / 2, 2) if not sale.is_igst else 0,
            "IgstVal":  round(gst_total, 2) if sale.is_igst else 0,
            "CesVal":   0,
            "StCesVal": 0,
            "Discount": 0,
            "OthChrg":  0,
            "RndOffAmt": 0,
            "TotInvVal": round(grand_total, 2),
        },
        "PayDtls": {
            "Nm":       sale.customer.name,
            "Mode":     sale.payment_mode.upper()[:10],
            "PayTerm":  "30",
            "PaidAmt":  float(sale.amount_received),
            "PaymtDue": float(sale.balance),
        },
        "EwbDtls": {
            "TransId":   sale.transporter_id or "",
            "TransName": sale.transporter_name or "",
            "TransMode": sale.transport_mode or "1",
            "Distance":  sale.transport_distance or 0,
            "TransDocNo": sale.transport or "",
            "TransDocDt": sale.date.strftime('%d/%m/%Y'),
            "VehNo":     sale.vehicle_number or "",
            "VehType":   "R",
        } if sale.vehicle_number or sale.transporter_id else None,
    }

    if request.GET.get('download'):
        import json
        resp = HttpResponse(
            json.dumps(payload, indent=2, default=str),
            content_type='application/json'
        )
        resp['Content-Disposition'] = f'attachment; filename="einvoice_{sale.bill_number}.json"'
        return resp

    return JsonResponse(payload, json_dumps_params={'indent': 2, 'default': str})


@login_req
def export_einvoice_csv(request):
    """
    Export sales as CSV in the NIC bulk e-invoice upload format.
    Download and upload to https://einvoice1.gst.gov.in for batch processing.
    """
    import csv
    from django.utils import timezone as tz
    godown = get_godown(request)

    # Filter: only tax invoices with bill number, not cash memos
    sales = Sale.objects.filter(
        godown=godown, sale_type='bill'
    ).select_related('customer').prefetch_related('items__product').order_by('-date')[:100]

    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="einvoice_bulk_upload.csv"'

    writer = csv.writer(resp)
    # NIC bulk upload format headers (simplified)
    writer.writerow([
        'Version', 'Supply Type', 'Doc Type', 'Doc No', 'Doc Date',
        'Seller GSTIN', 'Seller Name', 'Seller Address', 'Seller Pin', 'Seller State',
        'Buyer GSTIN', 'Buyer Name', 'Buyer Address', 'Buyer Pin', 'Buyer State',
        'Item Desc', 'HSN', 'Qty', 'UOM', 'Unit Price',
        'Taxable Value', 'GST Rate', 'IGST Amt', 'CGST Amt', 'SGST Amt',
        'Total Value', 'IRN', 'Ack No', 'e-Way Bill No',
    ])

    for sale in sales:
        for item in sale.items.all():
            taxable = float(item.qty_sqm * item.rate_per_sqm)
            gst_amt = taxable * float(sale.gst_rate) / 100
            writer.writerow([
                '1.1',
                'B2B' if sale.customer.gstin else 'B2C',
                'INV',
                sale.bill_number,
                sale.date.strftime('%d/%m/%Y'),
                godown.gstin or '',
                godown.firm_name,
                godown.address or '',
                '',
                godown.state_code or '32',
                sale.customer.gstin or sale.customer.gst_number or 'URP',
                sale.customer.name,
                sale.customer.location or '',
                sale.customer.pincode or '',
                sale.customer.state_code or '32',
                item.product.display_name,
                item.product.hsn_code or '4408',
                float(item.qty_sqm),
                item.product.uom or 'SQF',
                float(item.rate_per_sqm),
                round(taxable, 2),
                float(sale.gst_rate),
                round(gst_amt, 2) if sale.is_igst else 0,
                round(gst_amt / 2, 2) if not sale.is_igst else 0,
                round(gst_amt / 2, 2) if not sale.is_igst else 0,
                round(taxable + gst_amt, 2),
                sale.irn or '',
                sale.ack_number or '',
                sale.eway_bill_number or '',
            ])

    return resp


# ── Bank Accounts ─────────────────────────────────────────────────
@login_req
def bank_accounts(request):
    godown   = get_godown(request)
    accounts = BankAccount.objects.filter(godown=godown, is_active=True)
    return render(request, 'godown/bank_accounts.html', ctx(request, {
        'active': 'bank', 'accounts': accounts,
    }))


@login_req
def add_bank_account(request):
    godown = get_godown(request)
    if request.method == 'POST':
        errors = []
        name = request.POST.get('account_name','').strip()
        if not name: errors.append('Account name is required.')
        ob_raw = request.POST.get('opening_balance','0').strip() or '0'
        try:    opening_balance = Decimal(ob_raw)
        except: errors.append('Opening balance must be a number.'); opening_balance = Decimal('0')
        if errors:
            for e in errors: messages.error(request, e)
        else:
            BankAccount.objects.create(
                godown=godown,
                account_name=name,
                bank_name=request.POST.get('bank_name',''),
                account_no=request.POST.get('account_no',''),
                ifsc=request.POST.get('ifsc',''),
                upi_id=request.POST.get('upi_id',''),
                account_type=request.POST.get('account_type','current'),
                opening_balance=opening_balance,
            )
            messages.success(request, f'Bank account "{name}" added.')
            return redirect('bank_accounts')
    return render(request, 'godown/add_bank_account.html', ctx(request, {'active':'bank'}))


@login_req
def edit_bank_account(request, pk):
    godown  = get_godown(request)
    account = get_object_or_404(BankAccount, pk=pk, godown=godown)
    if request.method == 'POST':
        account.account_name    = request.POST.get('account_name', account.account_name)
        account.bank_name       = request.POST.get('bank_name','')
        account.account_no      = request.POST.get('account_no','')
        account.ifsc            = request.POST.get('ifsc','')
        account.upi_id          = request.POST.get('upi_id','')
        account.account_type    = request.POST.get('account_type','current')
        account.opening_balance = Decimal(request.POST.get('opening_balance','0') or '0')
        account.save()
        messages.success(request, 'Account updated.')
        return redirect('bank_statement', pk=pk)
    return render(request, 'godown/add_bank_account.html', ctx(request, {
        'active': 'bank', 'account': account,
    }))


@login_req
def bank_statement(request, pk):
    godown  = get_godown(request)
    account = get_object_or_404(BankAccount, pk=pk, godown=godown)

    # Date filter
    from_date = request.GET.get('from')
    to_date   = request.GET.get('to')
    txns = BankTransaction.objects.filter(account=account).select_related(
        'sale', 'grn', 'expense', 'recorded_by'
    )
    if from_date:
        txns = txns.filter(date__gte=from_date)
    if to_date:
        txns = txns.filter(date__lte=to_date)

    # Build running balance
    # Opening balance + all credits - all debits BEFORE from_date
    from django.db.models import Sum, Q
    from django.db.models.functions import Coalesce
    from django.db.models import DecimalField

    all_txns_before = BankTransaction.objects.filter(account=account)
    if from_date:
        all_txns_before = all_txns_before.filter(date__lt=from_date)
    credits_before = all_txns_before.filter(txn_type='credit').aggregate(
        t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
    debits_before  = all_txns_before.filter(txn_type='debit').aggregate(
        t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
    opening = account.opening_balance + credits_before - debits_before

    # Build ledger with running balance
    ledger = []
    balance = opening
    for txn in txns.order_by('date', 'created_at'):
        if txn.txn_type == 'credit':
            balance += txn.amount
        else:
            balance -= txn.amount
        ledger.append({
            'txn':     txn,
            'credit':  txn.amount if txn.txn_type == 'credit' else None,
            'debit':   txn.amount if txn.txn_type == 'debit'  else None,
            'balance': balance,
        })

    # Totals
    total_credit = sum(r['credit'] for r in ledger if r['credit'])
    total_debit  = sum(r['debit']  for r in ledger if r['debit'])
    closing      = opening + total_credit - total_debit

    return render(request, 'godown/bank_statement.html', ctx(request, {
        'active':       'bank',
        'account':      account,
        'ledger':       ledger,
        'opening':      opening,
        'closing':      closing,
        'total_credit': total_credit,
        'total_debit':  total_debit,
        'from_date':    from_date or '',
        'to_date':      to_date   or '',
    }))


@login_req
def add_bank_transaction(request, pk):
    godown  = get_godown(request)
    account = get_object_or_404(BankAccount, pk=pk, godown=godown)
    if request.method == 'POST':
        errors = []
        amt_raw = request.POST.get('amount','').strip()
        try:    amount = Decimal(amt_raw)
        except: amount = Decimal('0')
        if amount <= 0: errors.append('Amount must be greater than 0.')
        if not request.POST.get('date'): errors.append('Date is required.')
        if not request.POST.get('txn_type'): errors.append('Select Credit or Debit.')
        if not request.POST.get('description','').strip(): errors.append('Description is required.')
        if errors:
            for e in errors: messages.error(request, e)
        else:
            BankTransaction.objects.create(
                account=account,
                date=request.POST['date'],
                txn_type=request.POST['txn_type'],
                category=request.POST.get('category','other'),
                amount=amount,
                description=request.POST.get('description',''),
                reference=request.POST.get('reference',''),
                recorded_by=request.user,
            )
            messages.success(request, 'Transaction recorded.')
            return redirect('bank_statement', pk=pk)
    return render(request, 'godown/add_bank_transaction.html', ctx(request, {
        'active': 'bank', 'account': account,
    }))


# ── Service Vendor Payables (transport, forklift, labour etc.) ─────
@login_req
def vendor_payables(request):
    godown = get_godown(request)
    today  = timezone.now().date()
    vendors = Supplier.objects.filter(godown=godown, supplier_type__in=['service', 'both'])
    rows = []
    for v in vendors:
        expenses = LandingExpense.objects.filter(vendor=v, stock_in__godown=godown).select_related('stock_in')
        outstanding_expenses = [e for e in expenses if e.balance > 0]
        if outstanding_expenses:
            rows.append({
                'vendor': v,
                'expenses': outstanding_expenses,
                'total_owed': sum(e.balance for e in outstanding_expenses),
            })
    total_payable = sum(r['total_owed'] for r in rows)
    overdue_amount = sum(
        e.balance for r in rows for e in r['expenses']
        if e.stock_in.date + timedelta(days=30) < today
    )
    paid_month = LandingExpense.objects.filter(
        vendor__godown=godown, stock_in__date__month=today.month
    ).aggregate(t=db_models.Sum('amount_paid'))['t'] or Decimal('0')
    return render(request, 'godown/vendor_payables.html', ctx(request, {
        'active': 'vendor_payables',
        'rows': rows,
        'total_payable': total_payable,
        'overdue_amount': overdue_amount,
        'paid_month': paid_month,
    }))


@login_req
def record_vendor_payment(request, pk):
    """Record a payment against a specific landing expense (service charge)."""
    godown = get_godown(request)
    expense = get_object_or_404(
        LandingExpense.objects.select_related('stock_in', 'vendor'),
        pk=pk, stock_in__godown=godown
    )
    bank_accounts_qs = BankAccount.objects.filter(godown=godown, is_active=True)
    if request.method == 'POST':
        amt             = Decimal(request.POST.get('amount', 0) or 0)
        bank_account_id = request.POST.get('bank_account') or None
        reference       = request.POST.get('reference', '')
        date            = request.POST.get('date', str(timezone.now().date()))
        mode            = request.POST.get('payment_mode', 'cash')
        if amt <= 0:
            messages.error(request, 'Enter a valid payment amount.')
            return render(request, 'godown/record_vendor_payment.html', ctx(request, {
                'active': 'vendor_payables', 'expense': expense, 'bank_accounts': bank_accounts_qs,
            }))
        if amt > expense.balance + Decimal('0.01'):
            messages.error(request, f'Amount exceeds balance owed (₹{expense.balance:,.2f}).')
            return render(request, 'godown/record_vendor_payment.html', ctx(request, {
                'active': 'vendor_payables', 'expense': expense, 'bank_accounts': bank_accounts_qs,
            }))
        expense.amount_paid += amt
        expense.save(update_fields=['amount_paid'])

        vendor_bank_acc = None
        if bank_account_id:
            try:
                vendor_bank_acc = BankAccount.objects.get(pk=bank_account_id, godown=godown)
            except BankAccount.DoesNotExist:
                pass
        VendorPayment.objects.create(
            expense=expense, date=date, amount=amt,
            payment_mode=mode, reference=reference,
            bank_account=vendor_bank_acc, recorded_by=request.user,
        )

        if bank_account_id and mode != 'cash':
            try:
                bank_acc = BankAccount.objects.get(pk=bank_account_id, godown=godown)
                BankTransaction.objects.create(
                    account=bank_acc, date=date, txn_type='debit',
                    category='supplier_payment', amount=amt,
                    description=f'Payment to {expense.vendor.name} — {expense.get_category_display()} ({expense.stock_in.grn_number})',
                    reference=reference, recorded_by=request.user,
                )
            except BankAccount.DoesNotExist:
                pass

        messages.success(request, f'Payment of ₹{amt:,.0f} recorded for {expense.vendor.name}.')
        return redirect('vendor_payables')
    return render(request, 'godown/record_vendor_payment.html', ctx(request, {
        'active': 'vendor_payables', 'expense': expense, 'bank_accounts': bank_accounts_qs,
    }))


@login_req
def vendor_statement(request, pk):
    godown = get_godown(request)
    vendor = get_object_or_404(Supplier, pk=pk, godown=godown)
    expenses = LandingExpense.objects.filter(vendor=vendor, stock_in__godown=godown).select_related('stock_in').prefetch_related('payments').order_by('-stock_in__date')
    total_billed = sum(e.amount for e in expenses)
    total_paid   = sum(e.amount_paid for e in expenses)
    return render(request, 'godown/vendor_statement.html', ctx(request, {
        'active': 'vendor_payables', 'vendor': vendor, 'expenses': expenses,
        'total_billed': total_billed, 'total_paid': total_paid,
        'total_outstanding': total_billed - total_paid,
    }))


# ── Quick-add Service Vendor (inline from GRN landing expense row) ──
@login_req
def quick_add_vendor(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    godown = get_godown(request)
    name = request.POST.get('name', '').strip()
    if not name or len(name) < 2:
        return JsonResponse({'ok': False, 'error': 'Vendor name must be at least 2 characters.'}, status=400)
    if len(name) > 200:
        return JsonResponse({'ok': False, 'error': 'Vendor name is too long.'}, status=400)
    # Avoid exact-duplicate service vendors within the same godown
    existing = Supplier.objects.filter(
        godown=godown, name__iexact=name, supplier_type__in=['service', 'both']
    ).first()
    if existing:
        return JsonResponse({'ok': True, 'id': existing.pk, 'name': existing.name})
    vendor = Supplier.objects.create(
        godown=godown, name=name, supplier_type='service',
    )
    return JsonResponse({'ok': True, 'id': vendor.pk, 'name': vendor.name})
