# STANDARD LIBRARY
import os
from datetime import timedelta
from pathlib import Path

# THIRD PARTY
import environ

# DJANGO

# APPLICATION SPECIFIC


# ------------------------------------------------------------------------------
#       Project Paths
# ------------------------------------------------------------------------------
# Configures directories and filesystem path definitions used across
# the application settings.
# ------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------
#       Environment Configuration
# ------------------------------------------------------------------------------
# Initializes and sets up environment variable parsing via django-environ
# to ensure secure configurations.
# ------------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
    CORS_ALLOWED_ORIGINS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
)

environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

ENVIRONMENT = os.getenv('ENVIRONMENT', 'development').lower()
is_dev = ENVIRONMENT == 'development'

FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:3000' if is_dev else 'https://cubelogs-dashboard.vercel.app')
SUPPORT_EMAIL = env('SUPPORT_EMAIL', default='support@cubelogs.com')
COMPANY_NAME = env('COMPANY_NAME', default='CubeLogs Inc.')
COMPANY_WEBSITE = env('COMPANY_WEBSITE', default='https://cubelogs.com')


# ------------------------------------------------------------------------------
#       Security Configuration
# ------------------------------------------------------------------------------
# Contains base security parameters including keys, debug modes, host
# limits, and CORS origin controls.
# ------------------------------------------------------------------------------
SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env('ALLOWED_HOSTS')
CORS_ALLOW_ALL_ORIGINS = env('CORS_ALLOW_ALL_ORIGINS')
CORS_ALLOWED_ORIGINS = env('CORS_ALLOWED_ORIGINS')
if is_dev:
    dev_origins = ['http://localhost:3000', 'http://localhost:3001', 'http://127.0.0.1:3000', 'http://127.0.0.1:3001']
    for o in dev_origins:
        if o not in CORS_ALLOWED_ORIGINS:
            CORS_ALLOWED_ORIGINS.append(o)

CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env('CSRF_TRUSTED_ORIGINS', default=[
    'http://localhost:3000',
    'http://localhost:3001',
    'http://127.0.0.1:3000',
    'http://127.0.0.1:3001',
    'https://cubelogs-dashboard.vercel.app',
    'https://cubelogs-website.vercel.app',
])



# ------------------------------------------------------------------------------
#       Stripe Configuration
# ------------------------------------------------------------------------------
# Integration key settings for Stripe payment gateways.
# ------------------------------------------------------------------------------
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET')


# ------------------------------------------------------------------------------
#       Email Configuration
# ------------------------------------------------------------------------------
# Configures SMTP settings used by Django's email backend.
# Celery executes email sending asynchronously, but Django
# still uses these settings to connect to the SMTP server.
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env('EMAIL_PORT')
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = env('EMAIL_USE_TLS')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL')


# ------------------------------------------------------------------------------
#       Installed Applications
# ------------------------------------------------------------------------------
# Lists core Django apps, third-party libraries, and internal applications
# integrated into this project.
# ------------------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_celery_results',
    'core',
    'users',
    'attendance',
    'company',
    'tasks',
    'subscribers',
]


# ------------------------------------------------------------------------------
#       Middleware
# ------------------------------------------------------------------------------
# Order-dependent components processing request and response cycles.
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'core.middleware.APICSRFExemptMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'cubelogs.urls'
WSGI_APPLICATION = 'cubelogs.wsgi.application'


# ------------------------------------------------------------------------------
#       Templates
# ------------------------------------------------------------------------------
# Template engine configurations and processors.
# ------------------------------------------------------------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ------------------------------------------------------------------------------
#       Database Configuration
# ------------------------------------------------------------------------------
# Connection settings for relational database engines.
# ------------------------------------------------------------------------------
DATABASES = {
    'default': env.db('DATABASE_URL')
}

if not is_dev and DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "Production environment detected (ENVIRONMENT != 'development'), but DATABASE_URL is configured for SQLite. "
        "PostgreSQL (django.db.backends.postgresql) is required for production deployment."
    )

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ------------------------------------------------------------------------------
#       Authentication
# ------------------------------------------------------------------------------
# Validation rules and model hooks for handling secure password limits and users.
# ------------------------------------------------------------------------------
AUTH_USER_MODEL = 'users.Employee'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ------------------------------------------------------------------------------
#       REST Framework
# ------------------------------------------------------------------------------
# Configures Django REST Framework globally (authentication/permissions).
# ------------------------------------------------------------------------------
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=env("ACCESS_TOKEN_LIFETIME",cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=env("REFRESH_TOKEN_LIFETIME",cast=int)),
    'ROTATE_REFRESH_TOKENS': env("ROTATE_REFRESH_TOKENS",cast=bool),
    'BLACKLIST_AFTER_ROTATION': env("BLACKLIST_AFTER_ROTATION",cast=bool)
}

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    'DEFAULT_SCHEMA_CLASS': 'rest_framework.schemas.coreapi.AutoSchema',
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'


# ------------------------------------------------------------------------------
#       JWT Configuration
# ------------------------------------------------------------------------------
# Configures JSON Web Token lifetime scopes, refresh limits, and algorithms.
# ------------------------------------------------------------------------------
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,
    'JWK_URL': None,
    'LEEWAY': 0,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'rest_framework_simplejwt.authentication.default_user_authentication_rule',
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'TOKEN_USER_CLASS': 'rest_framework_simplejwt.models.TokenUser',
    'JTI_CLAIM': 'jti',
}


# ------------------------------------------------------------------------------
#       Internationalization
# ------------------------------------------------------------------------------
# Configures language mappings, local timezone boundaries, and localization.
# ------------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ------------------------------------------------------------------------------
#       Static & Media Files
# ------------------------------------------------------------------------------
# Physical folder definitions and URLs for serving system assets and files.
# ------------------------------------------------------------------------------
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ------------------------------------------------------------------------------
#       Celery Configuration
# ------------------------------------------------------------------------------
# Background broker targets, beat recurrence schedules, and queues.
# ------------------------------------------------------------------------------
CELERY_BROKER_URL = env('CELERY_BROKER_URL')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='django-db')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = is_dev

TEST_MODE = False

if TEST_MODE:
    CELERY_BEAT_SCHEDULE = {
        'sweep-workspace-subscriptions-every-15-seconds': {
            'task': 'company.tasks.sweep_workspace_subscriptions',
            'schedule': timedelta(seconds=15),
        },
    }
else:
    CELERY_BEAT_SCHEDULE = {
        'sweep-workspace-subscriptions-every-minute': {
            'task': 'company.tasks.sweep_workspace_subscriptions',
            'schedule': timedelta(minutes=1),
        },
    }


# ------------------------------------------------------------------------------
#       Logging
# ------------------------------------------------------------------------------
# Stream targets and filters for system debug and warning warnings logs.
# ------------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO' if is_dev else 'WARNING',
            'propagate': False,
        },
    },
}


# ------------------------------------------------------------------------------
#       Production Security
# ------------------------------------------------------------------------------
# Enhanced HTTP headers, cookies, and redirect configs enforced in production.
# ------------------------------------------------------------------------------
if not is_dev:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

