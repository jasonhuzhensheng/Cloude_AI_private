import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('PORTAL_DATA', BASE_DIR / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
keyfile = DATA / 'secret.key'
if not keyfile.exists():
    from django.core.management.utils import get_random_secret_key
    try:
        with keyfile.open('x') as f: f.write(get_random_secret_key())
        keyfile.chmod(0o600)
    except FileExistsError: pass
SECRET_KEY = keyfile.read_text()
DEBUG = False
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
CSRF_TRUSTED_ORIGINS = ['https://' + h for h in ALLOWED_HOSTS if h not in ('localhost','127.0.0.1','testserver')]
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','portal']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','portal.views.LoginThrottle']
ROOT_URLCONF = 'portal.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':DATA/'portal.sqlite3','OPTIONS':{'timeout':30}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':10}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR/'static-collected'
MEDIA_ROOT = DATA/'uploads'
LOGIN_URL = 'login'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.environ.get('PORTAL_INSECURE_LOCAL') != '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = 3600 if SESSION_COOKIE_SECURE else 0
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 86400 * 7
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 22*1024*1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024*1024
FORCE_SCRIPT_NAME = os.environ.get('PORTAL_PREFIX') or None
if FORCE_SCRIPT_NAME:
    SESSION_COOKIE_NAME = 'portal_setup_session'
    CSRF_COOKIE_NAME = 'portal_setup_csrf'
    SESSION_COOKIE_PATH = FORCE_SCRIPT_NAME + '/'
    CSRF_COOKIE_PATH = SESSION_COOKIE_PATH
MODEL_URL = os.environ.get('MODEL_URL','http://127.0.0.1:8080/proxy/absolute/8080/v1/chat/completions')
ENABLE_SETUP = os.environ.get('PORTAL_SETUP') == '1'
LOGGING = {'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'loggers':{'django.security.DisallowedHost':{'handlers':['console'],'level':'WARNING','propagate':False}}}

CSRF_FAILURE_VIEW = "portal.views.csrf_failure"

IMAGE_EDIT_READY = os.environ.get('IMAGE_EDIT_READY', '/workspace/private-ai/IMAGE_EDIT_READY')
IMAGE_EDIT_ROOT = os.environ.get('IMAGE_EDIT_ROOT', '/workspace/private-ai')

IDLE_SHUTDOWN_ENABLED = os.environ.get('PORTAL_IDLE_SHUTDOWN') == '1'
MIDDLEWARE.append('portal.idle.ActivityMiddleware')
TEMPLATES[0]['OPTIONS']['context_processors'].append('portal.idle.context')
