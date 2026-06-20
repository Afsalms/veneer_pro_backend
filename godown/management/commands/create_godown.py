"""
Management command: create_godown
Interactively prompts for all details and creates a new godown + admin user.

Usage:
    python manage.py create_godown
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from godown.models import Godown, UserProfile, GodownSequence


def prompt(label, default=None, required=True, secret=False):
    """Prompt the user for input with an optional default."""
    import getpass
    suffix = f' [{default}]' if default else ' (required)' if required else ' (optional, press Enter to skip)'
    while True:
        if secret:
            value = getpass.getpass(f'  {label}{suffix}: ').strip()
        else:
            value = input(f'  {label}{suffix}: ').strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ''
        print(f'  ⚠  {label} is required. Please enter a value.')


class Command(BaseCommand):
    help = 'Interactively create a new godown (company account) with an admin user'

    def handle(self, *args, **options):
        self.stdout.write('\n' + '─' * 50)
        self.stdout.write('  VeneerPro — Create New Godown')
        self.stdout.write('─' * 50 + '\n')

        # ── Company details ──────────────────────────────────────
        self.stdout.write('\n📦 Company Details\n')
        firm_name  = prompt('Firm / Company name')
        phone      = prompt('Phone number', required=False)
        email      = prompt('Email address', required=False)
        gstin      = prompt('GSTIN number', required=False)
        address    = prompt('Address', required=False)

        # ── Document prefixes ────────────────────────────────────
        self.stdout.write('\n🧾 Document Number Prefixes\n')
        self.stdout.write('  (These appear before invoice/PO/GRN numbers e.g. SL-1001)\n')
        inv_prefix = prompt('Invoice prefix', default='SL')
        po_prefix  = prompt('PO prefix',      default='PO')
        grn_prefix = prompt('GRN prefix',     default='GRN')

        # ── Bank details ─────────────────────────────────────────
        self.stdout.write('\n🏦 Bank Details (shown on invoices — optional)\n')
        bank_name  = prompt('Bank name',      required=False)
        account_no = prompt('Account number', required=False)
        ifsc       = prompt('IFSC code',      required=False)
        upi_id     = prompt('UPI ID',         required=False)

        # ── Admin user ───────────────────────────────────────────
        self.stdout.write('\n👤 Admin User\n')
        while True:
            username = prompt('Admin username')
            if User.objects.filter(username=username).exists():
                self.stdout.write(f'  ⚠  Username "{username}" already exists. Try another.')
            else:
                break

        while True:
            password = prompt('Admin password (min 6 chars)', secret=True)
            if len(password) < 6:
                self.stdout.write('  ⚠  Password too short. Must be at least 6 characters.')
            else:
                confirm = prompt('Confirm password', secret=True)
                if password != confirm:
                    self.stdout.write('  ⚠  Passwords do not match. Try again.')
                else:
                    break

        admin_email = prompt('Admin email', required=False)

        # ── Confirm ──────────────────────────────────────────────
        self.stdout.write('\n' + '─' * 50)
        self.stdout.write('  Review — please confirm:\n')
        self.stdout.write(f'  Firm:      {firm_name}')
        self.stdout.write(f'  GSTIN:     {gstin or "—"}')
        self.stdout.write(f'  Prefixes:  {inv_prefix}-XXXX / {po_prefix}-XXX / {grn_prefix}-XXX')
        self.stdout.write(f'  Admin:     {username}')
        self.stdout.write('─' * 50)

        confirm = input('\n  Create this godown? (yes/no): ').strip().lower()
        if confirm not in ('yes', 'y'):
            self.stdout.write('\n  Cancelled.\n')
            return

        # ── Create ───────────────────────────────────────────────
        godown = Godown.objects.create(
            firm_name      = firm_name,
            phone          = phone,
            email          = email,
            gstin          = gstin,
            address        = address,
            state_code     = '32',
            invoice_prefix = inv_prefix,
            po_prefix      = po_prefix,
            grn_prefix     = grn_prefix,
            bank_name      = bank_name,
            account_no     = account_no,
            ifsc           = ifsc,
            upi_id         = upi_id,
        )

        # Initialise sequences
        for seq_type in ('sale', 'po', 'grn', 'est'):
            GodownSequence.objects.create(godown=godown, seq_type=seq_type, last_num=0)

        # Seed lookup defaults
        from godown.management.commands.load_lookup_defaults import DEFAULTS
        from godown.models import LookupValue
        for category, rows in DEFAULTS.items():
            for value, label, sort_order, is_default in rows:
                LookupValue.objects.get_or_create(
                    godown=godown, category=category, value=value,
                    defaults={'label': label, 'sort_order': sort_order,
                              'is_default': is_default, 'is_active': True}
                )

        # Create admin user
        user = User.objects.create_user(
            username=username, password=password,
            email=admin_email, first_name='Admin',
        )
        UserProfile.objects.create(user=user, godown=godown, role='admin')

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Godown created successfully!\n'
            f'  Firm:     {godown.firm_name}  (ID: {godown.pk})\n'
            f'  Admin:    {username}\n'
            f'  Login at: http://your-server/login/\n'
        ))
