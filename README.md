# VeneerPro — Face Veneer Godown Management System

A full Django web application for managing a face veneer godown — mobile-first design.

## Features
- **Dashboard** — stock value, sales, receivables, payables, low stock alerts, payment dues
- **Customers & Suppliers** — manage parties with credit limits and outstanding tracking
- **Products** — catalogue with stock levels, buy/sale rates, reorder alerts
- **Stock In (GRN)** — multi-line item goods receipt with rack location tracking
- **Sales** — multi-line item billing with payment tracking
- **Receivables** — outstanding customer payments with collection recording
- **Payables** — supplier payment tracking
- **Expenses** — godown expense management with category breakdown
- **Analytics** — revenue vs cost charts, species breakdown, expense trends

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Install Django
pip install django

# 3. Run migrations
python manage.py migrate

# 4. Load demo data
python manage.py seed_data

# 5. Create admin user
python manage.py createsuperuser

# 6. Run server
python manage.py runserver
```

Open http://127.0.0.1:8000 in your browser.

Admin panel: http://127.0.0.1:8000/admin

## Demo Data
The `seed_data` command loads:
- 3 suppliers (Global Veneers, Southwood Veneers, Nature Veneers)
- 5 customers (Sunrise Furnitures, Kerala Wood Works, Classic Interiors, Greenline Decor, Premier Plyworks)
- 6 products (Teak 0.6mm, Oak 0.8mm, Walnut 0.6mm, Rosewood 0.5mm, Sapele 0.5mm, Wenge 0.8mm)
- 4 GRNs (stock receipts), 7 sales bills, 10 expense records

## Tech Stack
- Django 4.x / 6.x
- SQLite (default) — change to PostgreSQL in settings.py for production
- Chart.js for analytics charts
- Bootstrap Icons
- Mobile-first CSS (no Bootstrap dependency)
