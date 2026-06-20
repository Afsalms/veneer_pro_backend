from pathlib import Path
from dotenv import load_dotenv
import os

# Load .env from project root (same folder as manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

def env(key, default=None):
    return os.environ.get(key, default)

def env_bool(key, default=False):
    return env(key, str(default)).lower() in ('1', 'true', 'yes')

def env_list(key, default=''):
    val = env(key, default)
    return [v.strip() for v in val.split(',') if v.strip()]

# ── Core ──────────────────────────────────────────────────────────
SECRET_KEY  = env('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG       = env_bool('DEBUG', True)
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '*')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'godown',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'veneer_pro.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

WSGI_APPLICATION = 'veneer_pro.wsgi.application'

# ── Database ──────────────────────────────────────────────────────
# DB_ENGINE=sqlite   → SQLite  (default, for development)
# DB_ENGINE=postgres → PostgreSQL (for production)
_db_engine = env('DB_ENGINE', 'sqlite')

if _db_engine == 'postgres':
    DATABASES = {
        'default': {
            'ENGINE':       'django.db.backends.postgresql',
            'NAME':         env('DB_NAME',     'veneer_pro_db'),
            'USER':         env('DB_USER',     'veneer_user'),
            'PASSWORD':     env('DB_PASSWORD', ''),
            'HOST':         env('DB_HOST',     'localhost'),
            'PORT':         env('DB_PORT',     '5432'),
            'CONN_MAX_AGE': 60,
            'OPTIONS':      {'connect_timeout': 10},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME':   BASE_DIR / env('DB_NAME', 'db.sqlite3'),
        }
    }

# ── Internationalisation ──────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Asia/Kolkata'
USE_I18N      = True
USE_TZ        = True

# ── Static files ──────────────────────────────────────────────────
STATIC_URL       = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT      = env('STATIC_ROOT', os.path.join(BASE_DIR, 'staticdir'))

if not DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

# ── Auth ──────────────────────────────────────────────────────────
LOGIN_URL           = '/login/'
LOGIN_REDIRECT_URL  = '/'
LOGOUT_REDIRECT_URL = '/login/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Security (only enforced when DEBUG=False) ─────────────────────
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER      = True
    SECURE_CONTENT_TYPE_NOSNIFF    = True
    X_FRAME_OPTIONS                 = 'DENY'
    CSRF_COOKIE_SECURE              = True
    SESSION_COOKIE_SECURE           = True
    SECURE_SSL_REDIRECT             = env_bool('SECURE_SSL_REDIRECT', False)
    SECURE_HSTS_SECONDS             = int(env('SECURE_HSTS_SECONDS', 0))
    SECURE_HSTS_INCLUDE_SUBDOMAINS  = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)

# ── Message storage ───────────────────────────────────────────────
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'
