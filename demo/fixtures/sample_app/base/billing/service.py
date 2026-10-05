from dataclasses import dataclass


@dataclass
class Invoice:
    id: int
    amount_cents: int
    void: bool = False


def delete_invoice(invoice: Invoice) -> Invoice:
    """Void an invoice; billing records are never physically removed."""
    invoice.void = True
    return invoice
