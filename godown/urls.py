from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/',  views.login_view,  name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard
    path('', views.dashboard, name='dashboard'),

    # Users
    path('users/',               views.user_list,   name='user_list'),
    path('users/add/',           views.add_user,    name='add_user'),
    path('users/<int:pk>/edit/', views.edit_user,   name='edit_user'),
    path('users/<int:pk>/delete/', views.delete_user, name='delete_user'),

    # Customers
    path('customers/',                  views.customers,    name='customers'),
    path('customers/add/',              views.add_customer, name='add_customer'),
    path('customers/<int:pk>/edit/',    views.edit_customer,   name='edit_customer'),
    path('customers/<int:pk>/delete/', views.delete_customer, name='delete_customer'),
    path('customers/<int:pk>/statement/', views.customer_statement, name='customer_statement'),

    # Suppliers
    path('suppliers/',               views.suppliers,    name='suppliers'),
    path('suppliers/add/',           views.add_supplier, name='add_supplier'),
    path('suppliers/<int:pk>/edit/', views.edit_supplier,   name='edit_supplier'),
    path('suppliers/<int:pk>/delete/',  views.delete_supplier,  name='delete_supplier'),
    path('suppliers/<int:pk>/history/', views.supplier_history, name='supplier_history'),

    # Products
    path('products/',               views.products,    name='products'),
    path('products/add/',           views.add_product, name='add_product'),
    path('products/<int:pk>/edit/', views.edit_product,   name='edit_product'),
    path('products/<int:pk>/delete/', views.delete_product, name='delete_product'),

    # Purchase Orders
    path('purchase-orders/',               views.po_list,   name='po_list'),
    path('purchase-orders/add/',           views.add_po,    name='add_po'),
    path('purchase-orders/<int:pk>/',         views.po_detail,  name='po_detail'),
    path('purchase-orders/<int:pk>/edit/',    views.edit_po,    name='edit_po'),
    path('purchase-orders/<int:pk>/advance/', views.po_advance, name='po_advance'),

    # Stock In
    path('stock-in/',          views.stock_in_list, name='stock_in_list'),
    path('stock-in/add/',      views.add_stock_in,  name='add_stock_in'),
    path('stock-in/<int:pk>/', views.grn_detail,    name='grn_detail'),
    path('stock-in/<int:pk>/edit/', views.edit_grn, name='edit_grn'),

    # Sales
    path('sales/',                        views.sales_list,   name='sales_list'),
    path('sales/add/',                    views.add_sale,     name='add_sale'),
    path('sales/<int:pk>/',               views.sale_detail,         name='sale_detail'),
    path('sales/<int:pk>/edit/',          views.edit_sale,           name='edit_sale'),
    path('sales/<int:pk>/invoice/',       views.invoice_view,        name='invoice'),
    path('sales/<int:pk>/einvoice-json/',    views.einvoice_json,      name='einvoice_json'),
    path('sales/export-einvoice/',           views.export_einvoice_csv, name='export_einvoice_csv'),
    path('sales/<int:pk>/payment/',       views.record_sale_payment, name='record_sale_payment'),

    # Receivables
    path('receivables/', views.receivables, name='receivables'),

    # Payables
    path('payables/',               views.payables,       name='payables'),
    path('payables/pay/<int:pk>/',  views.record_payment, name='record_payment'),
    path('vendor-payables/',                    views.vendor_payables,        name='vendor_payables'),
    path('vendor-payables/pay/<int:pk>/',       views.record_vendor_payment,  name='record_vendor_payment'),
    path('vendor-payables/vendor/<int:pk>/',    views.vendor_statement,       name='vendor_statement'),

    # Expenses
    path('expenses/',           views.expenses,      name='expenses'),
    path('expenses/add/',       views.add_expense,   name='add_expense'),
    path('expenses/<int:pk>/edit/', views.edit_expense, name='edit_expense'),

    # Analytics
    path('analytics/',          views.analytics,        name='analytics'),
    path('grn-profit/',         views.grn_profit,       name='grn_profit'),
    path('stock-board/',        views.stock_board,      name='stock_board'),
    path('reorder-alerts/',     views.reorder_alerts,   name='reorder_alerts'),

    # Settings
    path('settings/', views.godown_settings, name='godown_settings'),

    # JSON APIs
    path('api/product/<int:pk>/',   views.product_api,      name='product_api'),
    path('api/quick-add-vendor/',   views.quick_add_vendor, name='quick_add_vendor'),
    path('api/analytics-data/',     views.analytics_data,   name='analytics_data'),
    path('api/profit-loss/',        views.profit_loss_data, name='profit_loss_data'),
    path('api/daily-cashflow/',     views.daily_cashflow,   name='daily_cashflow'),
    # Estimations
    path('estimations/',               views.estimations,      name='estimations'),
    path('estimations/add/',           views.add_estimation,   name='add_estimation'),
    path('estimations/<int:pk>/',      views.estimation_detail,name='estimation_detail'),
    path('estimations/<int:pk>/convert/', views.convert_to_sale, name='convert_to_sale'),

    # Stock damage
    path('damages/',               views.damage_list,   name='damage_list'),
    path('damages/add/',           views.add_damage,    name='add_damage'),
    path('damages/add/<int:grn_pk>/', views.add_damage, name='add_damage_grn'),
    path('damages/<int:pk>/delete/', views.delete_damage, name='delete_damage'),

    # PO items API (for GRN auto-populate)
    path('api/po/<int:pk>/items/',     views.po_items_api,     name='po_items_api'),

    # GST dashboard data
    path('api/gst-summary/',           views.gst_summary,      name='gst_summary'),
    path('gst-report/',                views.gst_report,       name='gst_report'),

    # Lookup table management (admin)
    path('settings/lookups/',              views.lookup_list,   name='lookup_list'),
    path('settings/lookups/add/',          views.lookup_add,    name='lookup_add'),
    path('settings/lookups/<int:pk>/edit/', views.lookup_edit,  name='lookup_edit'),
    path('settings/lookups/<int:pk>/toggle/', views.lookup_toggle, name='lookup_toggle'),
    path('settings/lookups/<int:pk>/delete/', views.lookup_delete, name='lookup_delete'),

    # Bank Accounts & Statement
    path('bank/',                              views.bank_accounts,       name='bank_accounts'),
    path('bank/add/',                          views.add_bank_account,    name='add_bank_account'),
    path('bank/<int:pk>/edit/',               views.edit_bank_account,   name='edit_bank_account'),
    path('bank/<int:pk>/statement/',          views.bank_statement,      name='bank_statement'),
    path('bank/<int:pk>/add-transaction/',    views.add_bank_transaction, name='add_bank_transaction'),

    # Lookup values API for dynamic forms
    path('api/lookups/<str:category>/',    views.lookup_api,    name='lookup_api'),

]
