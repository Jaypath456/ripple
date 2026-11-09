"""Synthetic P3 confirmation tasks, frozen before the pre-submission proof pass.

These were authored after diagnosing the development tasks in ``fixtures.py`` and
before implementing the change that diagnosis motivated. They are run once, for the
final matrix only, and were not used to choose any policy constant. They are still
author-written synthetic data, not an independent held-out benchmark.
"""

LIBRARY = {
    "books/__init__.py": "",
    "books/catalog.py": (
        "BOOKS = {}\n\n\n"
        "def add_book(isbn, title):\n"
        "    BOOKS[isbn] = {'title': title}\n\n\n"
        "def search_books(title):\n"
        '    """Find books whose title contains the text."""\n'
        "    return [b for b in BOOKS.values() if title in b['title']]\n"
    ),
    "books/fines.py": (
        "DAILY_RATE = 0.25\n\n\n"
        "def overdue_days(due, returned):\n"
        "    return max(0, (returned - due).days)\n"
    ),
    "books/loans.py": (
        "from books.catalog import BOOKS\n\n\n"
        "def checkout_book(isbn, member):\n"
        "    BOOKS[isbn]['member'] = member\n\n\n"
        "def return_book(isbn, returned):\n"
        '    """Mark a book as returned."""\n'
        "    BOOKS[isbn].pop('member', None)\n"
    ),
    "books/notify.py": (
        "def send_reminder(member, isbn):\n    return f'{member}: {isbn}'\n"
    ),
    "books/routes.py": (
        "from books.catalog import search_books\n"
        "from books.loans import return_book\n\n\n"
        "def search_route(query):\n"
        "    return search_books(query['title'])\n\n\n"
        "def return_route(payload):\n"
        "    return return_book(payload['isbn'], payload['returned'])\n"
    ),
    "tests/test_loans.py": (
        "from books.loans import checkout_book\n\n\n"
        "def test_checkout():\n"
        "    assert checkout_book is not None\n"
    ),
}

TELEMETRY = {
    "telemetry/__init__.py": "",
    "telemetry/counters.py": (
        "class Counter:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "        self.value = 0\n\n\n"
        "def increment(counter, amount=1):\n"
        "    counter.value += amount\n"
    ),
    "telemetry/export.py": (
        "from telemetry.counters import Counter\n\n\n"
        "def export_prometheus(counters):\n"
        '    """Render counters in Prometheus text format."""\n'
        "    return '\\n'.join(f'{c.name} {c.value}' for c in counters)\n"
    ),
    "telemetry/sampling.py": ("def sample_rate(service):\n    return 0.1\n"),
    "telemetry/http.py": (
        "from telemetry.export import export_prometheus\n\n\n"
        "def metrics_handler(registry):\n"
        "    return export_prometheus(registry)\n"
    ),
    "tests/test_export.py": (
        "from telemetry.counters import Counter\n"
        "from telemetry.export import export_prometheus\n\n\n"
        "def test_export():\n"
        "    assert export_prometheus([Counter('x')]) == 'x 0'\n"
    ),
}

AUTH = {
    "auth/__init__.py": "",
    "auth/settings.py": "SECRET = 'dev'\nALGORITHM = 'HS256'\n",
    "auth/tokens.py": (
        "import time\n\n"
        "from auth.settings import SECRET\n\n\n"
        "def issue_token(user):\n"
        '    """Issue an access token valid for one hour."""\n'
        "    return {'user': user, 'expires': time.time() + 3600, 'key': SECRET}\n\n\n"
        "def verify_token(token):\n"
        "    return token['expires'] > time.time()\n"
    ),
    "auth/sessions.py": (
        "from auth.tokens import issue_token\n\n\n"
        "def create_session(user):\n"
        "    return {'token': issue_token(user)}\n"
    ),
    "auth/passwords.py": (
        "import hashlib\n\n\n"
        "def hash_password(password):\n"
        '    """Hash a password after checking its length."""\n'
        "    if len(password) < 8:\n"
        "        raise ValueError('too short')\n"
        "    return hashlib.sha256(password.encode()).hexdigest()\n"
    ),
    "tests/test_tokens.py": (
        "from auth.tokens import issue_token, verify_token\n\n\n"
        "def test_token():\n"
        "    assert verify_token(issue_token('u'))\n"
    ),
}

REPOSITORIES = {"library": LIBRARY, "telemetry": TELEMETRY, "auth": AUTH}

TASKS = (
    {
        "task_id": "P3-CONF-01-LATE-FEE",
        "repository": "library",
        "kind": "multi-file",
        "request": "Charge a late fee when a book is returned after its due date.",
        "search_terms": ["late", "fee", "return_book", "due", "overdue"],
        "gold": ["books/fines.py", "books/loans.py"],
    },
    {
        "task_id": "P3-CONF-02-AUTHOR-SEARCH",
        "repository": "library",
        "kind": "single-file",
        "request": "Allow searching books by author name as well as title.",
        "search_terms": ["search_books", "author", "books", "title"],
        "gold": ["books/catalog.py"],
    },
    {
        "task_id": "P3-CONF-03-COUNTER-LABELS",
        "repository": "telemetry",
        "kind": "multi-file",
        "request": "Export counter labels in the Prometheus exporter output.",
        "search_terms": ["export_prometheus", "counter", "labels", "prometheus"],
        "gold": ["telemetry/counters.py", "telemetry/export.py"],
    },
    {
        "task_id": "P3-CONF-04-HELM-UPGRADE",
        "repository": "telemetry",
        "kind": "abstain: out of repository",
        "request": "Upgrade the Kubernetes ingress controller Helm chart to the "
        "latest version.",
        "search_terms": ["kubernetes", "ingress", "helm", "chart", "upgrade"],
        "gold": [],
    },
    {
        "task_id": "P3-CONF-05-TOKEN-EXPIRY",
        "repository": "auth",
        "kind": "multi-file",
        "request": "Make the access token expiry configurable in settings instead of "
        "a fixed one hour lifetime.",
        "search_terms": ["issue_token", "token", "expiry", "settings", "lifetime"],
        "gold": ["auth/settings.py", "auth/tokens.py"],
    },
    {
        "task_id": "P3-CONF-06-PASSWORD-DIGIT",
        "repository": "auth",
        "kind": "single-file",
        "request": "Require passwords to contain at least one digit when hashing a "
        "password.",
        "search_terms": ["hash_password", "password", "digit", "require"],
        "gold": ["auth/passwords.py"],
    },
)
