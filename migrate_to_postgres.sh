#!/bin/bash
# Run this once to migrate data from SQLite to PostgreSQL
# Requires: pip install psycopg2-binary

set -e
echo "=== VeneerPro: SQLite → PostgreSQL Migration ==="

# Step 1: Export data from SQLite
echo "Exporting data from SQLite..."
DJANGO_SETTINGS_MODULE=veneer_pro.settings python manage.py dumpdata \
    --natural-foreign --natural-primary \
    --exclude=contenttypes --exclude=auth.Permission \
    > /tmp/veneer_data.json
echo "  Exported $(python -c "import json; d=json.load(open('/tmp/veneer_data.json')); print(len(d), 'records')")"

# Step 2: Set up PostgreSQL DB (run as postgres user)
echo "Creating PostgreSQL database..."
# psql -U postgres -c "CREATE USER veneer_user WITH PASSWORD 'your_password';"
# psql -U postgres -c "CREATE DATABASE veneer_pro_db OWNER veneer_user;"
# psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE veneer_pro_db TO veneer_user;"

# Step 3: Run migrations on PostgreSQL
echo "Running migrations on PostgreSQL..."
DJANGO_SETTINGS_MODULE=veneer_pro.settings_production python manage.py migrate --run-syncdb

# Step 4: Load data into PostgreSQL
echo "Loading data into PostgreSQL..."
DJANGO_SETTINGS_MODULE=veneer_pro.settings_production python manage.py loaddata /tmp/veneer_data.json

echo "=== Migration complete ==="
echo "Test with: DJANGO_SETTINGS_MODULE=veneer_pro.settings_production python manage.py check"
