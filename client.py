#!/usr/bin/env python3
"""
Cliente demo: un mini-agente de IA que paga con x402 para leer los niveles de HYPE.

Pasos:
  1. Pide /niveles sin pagar -> recibe un 402 con los requisitos.
  2. Crea una billetera TEMPORAL en memoria (nunca se guarda nada en disco).
  3. Firma una autorizacion EIP-3009 (transferWithAuthorization de USDC).
  4. Reintenta /niveles con el header X-PAYMENT y recibe los datos.

Uso:
  python client.py                        # flujo completo contra localhost:8000
  python client.py --servidor http://localhost:8000
  python client.py --mostrar-pago         # solo firma y muestra el header, sin enviar
  python client.py --monto 0.001          # intenta pagar menos (el servidor lo rechaza)

SIN plata real: la billetera es efimera y la liquidacion del servidor es
simulada, asi que no se mueve ningun fondo. No hace falta faucet para
probar esta demo.
"""
import argparse
import base64
import json
import os
import time
import urllib.request

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

RED = "base-sepolia"
USDC = to_checksum_address("0x036CbD53842c5426634e7929541eC2318f3dCF7e")

TIPOS = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


def pedir(url, header_pago=None):
    req = urllib.request.Request(url)
    if header_pago:
        req.add_header("X-PAYMENT", header_pago)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read()), resp.headers.get("X-PAYMENT-RESPONSE")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), None


def main():
    parser = argparse.ArgumentParser(description="Agente demo que paga con x402")
    parser.add_argument("--servidor", default="http://localhost:8000")
    parser.add_argument("--monto", default="0.01", help="USDC a pagar (por defecto 0.01)")
    parser.add_argument("--mostrar-pago", action="store_true",
                        help="solo firma y muestra el header X-PAYMENT, sin enviarlo")
    args = parser.parse_args()
    base = args.servidor.rstrip("/")
    monto_unidades = int(float(args.monto) * 1_000_000)

    print("[agente] 1. Pido /niveles sin pagar...")
    codigo, cuerpo, _ = pedir(base + "/niveles")
    print(f"[agente]    -> el servidor responde {codigo} (Payment Required)")
    if codigo != 402:
        print(f"[agente]    respuesta inesperada: {cuerpo}")
        return
    acepta = cuerpo["accepts"][0]
    print(f"[agente]    pide {acepta['maxAmountRequired']} unidades de USDC "
          f"({int(acepta['maxAmountRequired']) / 1_000_000:.2f} USDC) en {acepta['network']}")
    print(f"[agente]    a pagar a: {acepta['payTo']}")
    print(f"[agente]    moneda: {acepta['asset']}")
    if acepta["asset"].lower() != USDC.lower():
        print("[agente]    OJO: la moneda no es el USDC oficial de Base Sepolia. No pago.")
        return

    # Billetera temporal: solo vive en memoria durante esta ejecucion.
    billetera = Account.create()
    print(f"[agente] 2. Creo una billetera temporal: {billetera.address}")

    ahora = int(time.time())
    nonce = os.urandom(32)
    auth = {
        "from": billetera.address,
        "to": acepta["payTo"],
        "value": monto_unidades,
        "validAfter": 0,
        "validBefore": ahora + 600,
        "nonce": "0x" + nonce.hex(),
    }
    mensaje = {
        "types": TIPOS,
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 84532,
                   "verifyingContract": USDC},
        "message": {**auth, "nonce": nonce},
    }
    firmado = billetera.sign_message(encode_typed_data(full_message=mensaje))
    header_pago = base64.b64encode(json.dumps({
        "x402Version": 1,
        "scheme": "exact",
        "network": RED,
        "payload": {"signature": "0x" + firmado.signature.hex(), "authorization": auth},
    }).encode()).decode()
    print("[agente] 3. Firme la autorizacion EIP-3009 (transferWithAuthorization).")

    if args.mostrar_pago:
        print("\n--- Header X-PAYMENT (copialo para usarlo con curl) ---")
        print(header_pago)
        print("\n--- Comando curl para pagar y recibir los niveles ---")
        print(f'curl -s -H "X-PAYMENT: {header_pago}" {base}/niveles')
        print("\n(OJO: cada pago solo sirve una vez, el nonce no se puede reutilizar.)")
        return

    print("[agente] 4. Reintento /niveles con el header X-PAYMENT...")
    codigo, cuerpo, resp_pago = pedir(base + "/niveles", header_pago)
    if codigo != 200:
        print(f"[agente]    el servidor lo rechazo ({codigo}): {cuerpo}")
        return
    print("[agente] 5. Pago aceptado. Datos recibidos:")
    print(json.dumps(cuerpo, indent=2, ensure_ascii=False))
    if resp_pago:
        print("[agente]    X-PAYMENT-RESPONSE:",
              json.loads(base64.b64decode(resp_pago)))
    print("\n[agente] --- Header X-PAYMENT usado (para reintentarlo con curl) ---")
    print(header_pago)
    print("(Si lo reusas con curl, el servidor lo rechaza: el nonce ya esta usado.)")


if __name__ == "__main__":
    main()
