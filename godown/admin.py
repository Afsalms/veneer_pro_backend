from django.contrib import admin
from .models import Customer, Supplier, Product, StockIn, StockInItem, Sale, SaleItem, Expense

class StockInItemInline(admin.TabularInline):
    model = StockInItem
    extra = 1

class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'location', 'credit_limit']
    search_fields = ['name', 'phone']

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'city', 'species_supplied']
    search_fields = ['name']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['species', 'thickness', 'cut_type', 'stock_qty', 'buy_rate', 'sale_rate']
    list_filter = ['thickness', 'cut_type']

@admin.register(StockIn)
class StockInAdmin(admin.ModelAdmin):
    list_display = ['grn_number', 'supplier', 'date', 'amount_paid']
    inlines = [StockInItemInline]

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['bill_number', 'customer', 'date', 'amount_received']
    inlines = [SaleItemInline]

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['category', 'date', 'description', 'amount', 'status']
    list_filter = ['category', 'status']
