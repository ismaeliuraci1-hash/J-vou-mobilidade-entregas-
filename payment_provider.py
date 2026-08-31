"""Adaptador opcional para Pix real via Mercado Pago.

Sem MERCADOPAGO_ACCESS_TOKEN, o backend continua no modo sandbox.
Nunca coloque o token no HTML ou no JavaScript do navegador.
"""
import json
import os
import secrets
import urllib.request


def create_pix(amount_cents, description, payer_email):
    access_token = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    if not access_token:
        return None
    payload = {
        "transaction_amount": round(amount_cents / 100, 2),
        "description": description[:200],
        "payment_method_id": "pix",
        "payer": {"email": payer_email or "pagador@javou.local"},
    }
    request = urllib.request.Request(
        "https://api.mercadopago.com/v1/payments",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
            "X-Idempotency-Key": secrets.token_hex(16),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read())
    transaction = data.get("point_of_interaction", {}).get("transaction_data", {})
    return {
        "provider": "mercadopago",
        "provider_id": str(data.get("id", "")),
        "status": data.get("status", "pending"),
        "pix_code": transaction.get("qr_code"),
        "qr_code_base64": transaction.get("qr_code_base64"),
    }
