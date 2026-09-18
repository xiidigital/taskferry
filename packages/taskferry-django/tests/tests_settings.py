"""Minimal Django settings for the taskferry-django test suite.

Deliberately small: the point of these tests is the bridge, not Django. There is
no database engine in use beyond the in-memory SQLite that ``transaction.atomic``
needs, no middleware, and no app beyond ``taskferry_django`` itself.
"""

from __future__ import annotations

SECRET_KEY = "taskferry-tests-not-a-real-secret"
DEBUG = True
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "taskferry_django",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# The whole Django surface Taskferry touches: one standard TASKS alias pointed at
# the bridge, and one TASKFERRY dict describing where work actually goes.
TASKS = {
    "default": {"BACKEND": "taskferry_django.TaskferryBackend"},
    "pinned": {
        "BACKEND": "taskferry_django.TaskferryBackend",
        "OPTIONS": {"backend": "thread"},
    },
}

TASKFERRY = {
    "backends": {
        "inline": {"factory": "inline"},
        "thread": {"factory": "thread"},
        "subprocess": {"factory": "subprocess"},
    },
    "routes": [
        {"kind": "task", "queue": "immediate", "backend": "thread"},
    ],
    "defaults": {
        "inline": "inline",
        "task": "thread",
        "job": "subprocess",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
