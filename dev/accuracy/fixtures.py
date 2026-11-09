"""Synthetic P3 development tasks: authored for this sprint, never held out.

Every repository, request, interpreted search-term list and gold label below was
written before any configuration was run, and none is derived from the frozen
FEA-Bench evaluation. Gold lists the existing source files a correct change edits;
an empty gold list means the correct behaviour is to abstain. Search terms stand in
for the model's interpretation step and are identical for every configuration.
"""

SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": (
        "DISCOUNT_CODES = {'SPRING': 0.1, 'VIP': 0.2}\n\n\n"
        "def unit_price(item):\n"
        "    return item['price']\n\n\n"
        "def apply_discount(total, code):\n"
        '    """Apply a percentage discount code to an order total."""\n'
        "    return total * (1 - DISCOUNT_CODES.get(code, 0))\n"
    ),
    "shop/shipping.py": (
        "def shipping_cost(weight, zone):\n"
        '    """Flat shipping rate by zone and weight."""\n'
        "    return 5 + weight * (2 if zone == 'remote' else 1)\n"
    ),
    "shop/tax.py": (
        "RATES = {'NY': 0.08, 'CA': 0.0725}\n\n\n"
        "def sales_tax(amount, region):\n"
        '    """Sales tax owed on an amount for a region."""\n'
        "    return amount * RATES.get(region, 0)\n"
    ),
    "shop/cart.py": (
        "from shop.pricing import apply_discount, unit_price\n"
        "from shop.shipping import shipping_cost\n"
        "from shop.tax import sales_tax\n\n\n"
        "def cart_total(items, code, zone, region):\n"
        "    subtotal = apply_discount(sum(unit_price(i) for i in items), code)\n"
        "    weight = sum(i['weight'] for i in items)\n"
        "    return subtotal + sales_tax(subtotal, region) + shipping_cost(weight, zone)\n"
    ),
    "shop/api.py": (
        "from shop.cart import cart_total\n\n\n"
        "def checkout_handler(request):\n"
        "    return {'total': cart_total(request['items'], request.get('code'),"
        " request['zone'], request['region'])}\n"
    ),
    "tests/test_pricing.py": (
        "from shop.pricing import apply_discount\n\n\n"
        "def test_apply_discount():\n"
        "    assert apply_discount(100, 'SPRING') == 90\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import cart_total\n\n\n"
        "def test_cart_total():\n"
        "    items = [{'price': 10, 'weight': 1}]\n"
        "    assert cart_total(items, None, 'local', 'XX') == 16\n"
    ),
}

TICKETS = {
    "tickets/__init__.py": "",
    "tickets/models.py": (
        "class Ticket:\n"
        "    def __init__(self, title, status='open'):\n"
        "        self.title = title\n"
        "        self.status = status\n\n\n"
        "def new_ticket(title):\n"
        "    return Ticket(title)\n"
    ),
    "tickets/storage.py": (
        "_RECORDS = {}\n\n\n"
        "def save(record):\n"
        "    _RECORDS[id(record)] = record\n"
        "    return id(record)\n\n\n"
        "def load(record_id):\n"
        "    return _RECORDS[record_id]\n"
    ),
    "tickets/audit.py": (
        "EVENTS = []\n\n\n"
        "def audit_log(event):\n"
        '    """Append an audit event."""\n'
        "    EVENTS.append(event)\n"
    ),
    "tickets/service.py": (
        "from tickets.audit import audit_log\n"
        "from tickets.models import Ticket, new_ticket\n"
        "from tickets.storage import load, save\n\n\n"
        "def open_ticket(title):\n"
        "    ticket = new_ticket(title)\n"
        "    save(ticket)\n"
        "    audit_log('opened')\n"
        "    return ticket\n\n\n"
        "def close_ticket(ticket_id):\n"
        "    ticket = load(ticket_id)\n"
        "    ticket.status = 'closed'\n"
        "    return ticket\n"
    ),
    "tickets/api.py": (
        "from tickets.service import close_ticket, open_ticket\n\n\n"
        "def create_ticket_endpoint(payload):\n"
        "    return {'title': open_ticket(payload['title']).title}\n\n\n"
        "def close_ticket_endpoint(ticket_id):\n"
        "    return {'status': close_ticket(ticket_id).status}\n"
    ),
    "tests/test_service.py": (
        "from tickets.service import open_ticket\n\n\n"
        "def test_open_ticket():\n"
        "    assert open_ticket('x').status == 'open'\n"
    ),
}

PROFILES = {
    "users/__init__.py": "",
    "users/models.py": (
        "class User:\n"
        "    def __init__(self, first, last):\n"
        "        self.first = first\n"
        "        self.last = last\n"
    ),
    "users/display.py": (
        "def format_name(user):\n"
        '    """Display name for a user."""\n'
        "    return f'{user.first} {user.last}'\n\n\n"
        "def avatar_url(user):\n"
        "    return f'/avatars/{user.last}.png'\n"
    ),
    "users/views.py": (
        "from users.display import format_name\n\n\n"
        "def profile_view(user):\n"
        "    return {'name': format_name(user)}\n"
    ),
    "reports/__init__.py": "",
    "reports/display.py": (
        "def format_name(report):\n"
        '    """Title-case a report name for display."""\n'
        "    return report.title()\n"
    ),
    "reports/builder.py": (
        "from reports.display import format_name\n\n\n"
        "def build_report(name, rows):\n"
        "    return {'name': format_name(name), 'rows': rows}\n"
    ),
    "tests/test_users.py": (
        "from users.display import format_name\n"
        "from users.models import User\n\n\n"
        "def test_format_name():\n"
        "    assert format_name(User('Ada', 'Lovelace')) == 'Ada Lovelace'\n"
    ),
}

RETRY = {
    "core/__init__.py": "",
    "core/settings.py": "MAX_ATTEMPTS = 5\nBASE_DELAY = 0.5\n",
    "core/a.py": (
        "from core import b\n"
        "from core.settings import MAX_ATTEMPTS\n\n\n"
        "def retry_call(func):\n"
        '    """Call func, retrying with exponential backoff."""\n'
        "    for attempt in range(MAX_ATTEMPTS):\n"
        "        try:\n"
        "            return func()\n"
        "        except OSError:\n"
        "            b.wait(attempt)\n"
    ),
    "core/b.py": (
        "from core import a\n"
        "from core.settings import BASE_DELAY\n\n\n"
        "def backoff_delay(attempt):\n"
        '    """Exponential backoff delay for an attempt."""\n'
        "    return BASE_DELAY * 2**attempt\n\n\n"
        "def wait(attempt):\n"
        "    return backoff_delay(attempt)\n\n\n"
        "def retry_wrapper(func):\n"
        "    return a.retry_call(func)\n"
    ),
    "core/c.py": (
        "from core.a import retry_call\n"
        "from core.b import retry_wrapper\n\n\n"
        "def fetch(url):\n"
        "    return retry_call(lambda: url)\n"
    ),
    **{
        f"core/client_{n}.py": (
            f"from core.c import fetch\n\n\ndef client_{n}_get(path):\n"
            "    return fetch(path)\n"
        )
        for n in range(6)
    },
    "tests/test_backoff.py": (
        "from core.b import backoff_delay\n\n\n"
        "def test_backoff_delay():\n"
        "    assert backoff_delay(1) == 1.0\n"
    ),
}

BILLING = {
    "billing/__init__.py": "",
    "billing/formatting.py": (
        "def render_amount(value):\n    return f'${value:.2f}'\n"
    ),
    "billing/invoice.py": (
        "from billing.formatting import render_amount\n\n\n"
        "def invoice_total(lines):\n"
        '    """Sum invoice line totals."""\n'
        "    return sum(line['amount'] for line in lines)\n\n\n"
        "def invoice_summary(invoice):\n"
        "    return {'total': render_amount(invoice_total(invoice['lines']))}\n"
    ),
    "billing/ledger.py": (
        "def post_entry(account, value):\n"
        "    return {'account': account, 'value': value}\n"
    ),
    "tests/test_invoice.py": (
        "from billing.invoice import invoice_total\n\n\n"
        "def test_invoice_total():\n"
        "    assert invoice_total([{'amount': 2}]) == 2\n"
    ),
}

INVENTORY = {
    "services/__init__.py": "",
    "services/inventory/__init__.py": "",
    "services/inventory/warehouse_configuration.py": (
        "THRESHOLDS = {'default': 5}\n\n\n"
        "def threshold_for(sku):\n"
        "    return THRESHOLDS.get(sku, THRESHOLDS['default'])\n"
    ),
    "services/inventory/stock_levels.py": (
        "def stock_level(on_hand, reserved):\n"
        '    """Classify stock as out, low or available."""\n'
        "    free = max(0, on_hand - reserved)\n"
        "    return 'out' if free == 0 else 'available'\n"
    ),
    "services/inventory/alerts.py": (
        "from services.inventory.stock_levels import stock_level\n\n\n"
        "def send_alert(sku, on_hand, reserved):\n"
        "    return {'sku': sku, 'level': stock_level(on_hand, reserved)}\n"
    ),
    "tests/test_stock_levels.py": (
        "from services.inventory.stock_levels import stock_level\n\n\n"
        "def test_stock_level():\n"
        "    assert stock_level(0, 0) == 'out'\n"
    ),
}

REPOSITORIES = {
    "shop": SHOP,
    "tickets": TICKETS,
    "profiles": PROFILES,
    "retry": RETRY,
    "billing": BILLING,
    "inventory": INVENTORY,
}

TASKS = (
    {
        "task_id": "P3-DEV-01-LOYALTY-DISCOUNT",
        "repository": "shop",
        "kind": "single-file",
        "request": "Add a loyalty discount code that applies a percentage discount "
        "to the order total.",
        "search_terms": ["loyalty", "discount", "code", "percentage", "apply_discount"],
        "gold": ["shop/pricing.py"],
    },
    {
        "task_id": "P3-DEV-02-TAX-ON-SHIPPING",
        "repository": "shop",
        "kind": "multi-file",
        "request": "Charge sales tax on shipping costs in the cart total for taxable "
        "regions.",
        "search_terms": ["sales_tax", "shipping", "cart_total", "tax", "region"],
        "gold": ["shop/cart.py", "shop/tax.py"],
    },
    {
        "task_id": "P3-DEV-03-TICKET-PRIORITY",
        "repository": "tickets",
        "kind": "multi-file",
        "request": "Add a priority level to tickets so the create ticket endpoint "
        "accepts it and the ticket service stores it on new tickets.",
        "search_terms": ["priority", "ticket", "create_ticket_endpoint", "new_ticket"],
        "gold": ["tickets/api.py", "tickets/models.py", "tickets/service.py"],
    },
    {
        "task_id": "P3-DEV-04-AUDIT-CLOSE",
        "repository": "tickets",
        "kind": "single-file",
        "request": "Record an audit event when a ticket is closed.",
        "search_terms": ["audit", "close_ticket", "closed", "ticket"],
        "gold": ["tickets/service.py"],
    },
    {
        "task_id": "P3-DEV-05-MIDDLE-NAME",
        "repository": "profiles",
        "kind": "multi-file, similar symbol names",
        "request": "Show a user's middle name when formatting the user display name.",
        "search_terms": ["format_name", "middle", "user", "display", "name"],
        "gold": ["users/display.py", "users/models.py"],
    },
    {
        "task_id": "P3-DEV-06-BACKOFF-CAP",
        "repository": "retry",
        "kind": "multi-file, cyclic dependency graph",
        "request": "Cap the exponential backoff delay used by retry_call at a "
        "configurable maximum delay setting.",
        "search_terms": ["backoff", "delay", "retry_call", "maximum", "setting"],
        "gold": ["core/b.py", "core/settings.py"],
    },
    {
        "task_id": "P3-DEV-07-INVOICE-CURRENCY",
        "repository": "billing",
        "kind": "multi-file, structural-only second file",
        "request": "Display invoice totals in the customer's currency instead of "
        "always using dollars.",
        "search_terms": ["invoice", "currency", "total", "customer", "dollars"],
        "gold": ["billing/formatting.py", "billing/invoice.py"],
    },
    {
        "task_id": "P3-DEV-08-LOW-STOCK-THRESHOLD",
        "repository": "inventory",
        "kind": "multi-file, long paths",
        "request": "Report low stock when free units fall below the configured stock "
        "threshold for the sku.",
        "search_terms": ["stock_level", "low", "threshold", "stock", "sku"],
        "gold": [
            "services/inventory/stock_levels.py",
            "services/inventory/warehouse_configuration.py",
        ],
    },
    {
        "task_id": "P3-DEV-09-IAM-ROTATION",
        "repository": "tickets",
        "kind": "abstain: out of repository",
        "request": "Rotate the AWS IAM access keys used by the deployment pipeline.",
        "search_terms": ["aws", "iam", "access", "keys", "rotate", "deployment"],
        "gold": [],
    },
    {
        "task_id": "P3-DEV-10-README-TYPO",
        "repository": "shop",
        "kind": "abstain: no relevant source",
        "request": "Fix the typo in the README installation section.",
        "search_terms": ["readme", "typo", "installation", "section"],
        "gold": [],
    },
)
