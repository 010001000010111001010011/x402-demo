#!/usr/bin/env python3
"""
Servidor demo x402: vende los niveles de HYPE en vivo por 0,01 USDC (testnet).

Como funciona el protocolo x402 real (esquema "exact" con EIP-3009):
  1. El cliente pide /niveles sin pagar -> el servidor responde 402
     con los requisitos de pago (red, monto, moneda, a quien pagar).
  2. El cliente firma una autorizacion EIP-3009 (transferWithAuthorization
     de USDC) y la manda en el header X-PAYMENT.
  3. El servidor verifica la firma (EIP-712), el monto, la vigencia y
     que el nonce no se haya usado -> entrega los datos con el header
     X-PAYMENT-RESPONSE.

SIN plata real:
  - La verificacion de la firma SI es real (criptografia EIP-712).
  - La LIQUIDACION on-chain esta SIMULADA: no se transmite ninguna
    transaccion a la red. El servidor solo marca el nonce como usado
    en memoria, para que no se pueda reutilizar el mismo pago.
  - El chequeo de nonce en cadena (eth_call a authorizationState) se
    intenta contra el RPC publico de Base Sepolia; si el RPC no
    responde, se sigue solo con las verificaciones locales y queda
    registrado en la respuesta.

No se guardan claves ni semillas en ningun archivo.
"""
import base64
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address

# ---------------------------------------------------------------- config
PORT = int(os.environ.get("PORT", "8000"))
NETWORK = "base-sepolia"
CHAIN_ID = 84532
USDC = to_checksum_address("0x036CbD53842c5426634e7929541eC2318f3dCF7e")
PRECIO_UNIDADES = int(os.environ.get("PRECIO_UNIDADES", "10000"))  # 0,01 USDC (6 decimales)
PAY_TO = to_checksum_address(
    os.environ.get("PAY_TO", "0x000000000000000000000000000000000000dEaD")
)
RPC_URL = os.environ.get("RPC_URL", "https://sepolia.base.org")
HYPERLIQUID_URL = "https://api.hyperliquid.xyz/info"
MAX_TIMEOUT_SECONDS = 60

# Nonces ya usados (en memoria: al reiniciar el servidor se olvidan).
nonces_usados = set()
candado = threading.Lock()

EIP712_DOMAIN = {
    "name": "USD Coin",
    "version": "2",
    "chainId": CHAIN_ID,
    "verifyingContract": USDC,
}
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


# ---------------------------------------------------------------- helpers
def responder(handler, codigo, cuerpo, headers_extra=None):
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(datos)))
    for k, v in (headers_extra or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(datos)


def requisitos_pago(recurso):
    """El cuerpo 402 segun el protocolo x402."""
    return {
        "x402Version": 1,
        "error": "Payment Required",
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "maxAmountRequired": str(PRECIO_UNIDADES),
                "resource": recurso,
                "description": "Niveles de HYPE en vivo (precio actual desde Hyperliquid)",
                "mimeType": "application/json",
                "payTo": PAY_TO,
                "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
                "asset": USDC,
                "extra": {"name": "USDC", "version": "2"},
            }
        ],
    }


def selector(firma):
    return keccak(text=firma)[:4].hex()


def nonce_usado_en_cadena(direccion, nonce_hex):
    """Lee authorizationState(from, nonce) del contrato USDC por RPC.

    Devuelve True si el nonce YA se uso en cadena, False si esta libre,
    o None si el RPC no respondio (no se pudo verificar).
    """
    try:
        datos = (
            selector("authorizationState(address,bytes32)")
            + direccion[2:].lower().rjust(64, "0")
            + nonce_hex[2:].lower().rjust(64, "0")
        )
        cuerpo = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": USDC, "data": "0x" + datos}, "latest"],
            }
        ).encode()
        req = urllib.request.Request(
            RPC_URL, data=cuerpo, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read())
        if "result" not in res:
            return None
        return int(res["result"], 16) != 0
    except Exception as exc:  # RPC caido o sin internet: no bloquea la demo
        print(f"[aviso] no se pudo consultar el RPC ({exc}); sigo con verificacion local")
        return None


def verificar_pago(header_pago):
    """Verifica un header X-PAYMENT. Devuelve (ok, detalle_o_error, autorizacion)."""
    try:
        pago = json.loads(base64.b64decode(header_pago))
    except Exception:
        return False, "header X-PAYMENT no es JSON en base64", None
    if pago.get("x402Version") != 1 or pago.get("scheme") != "exact":
        return False, "version o esquema x402 no soportado", None
    if pago.get("network") != NETWORK:
        return False, f"red incorrecta: se esperaba {NETWORK}", None
    carga = pago.get("payload") or {}
    firma = carga.get("signature")
    auth = carga.get("authorization") or {}
    for campo in ("from", "to", "value", "validAfter", "validBefore", "nonce"):
        if campo not in auth:
            return False, f"falta el campo {campo} en la autorizacion", None
    try:
        de = to_checksum_address(auth["from"])
        a = to_checksum_address(auth["to"])
        valor = int(auth["value"])
        valido_desde = int(auth["validAfter"])
        valido_hasta = int(auth["validBefore"])
        nonce = bytes.fromhex(auth["nonce"][2:] if auth["nonce"].startswith("0x") else auth["nonce"])
    except Exception:
        return False, "campos de la autorizacion con formato invalido", None
    if len(nonce) != 32:
        return False, "el nonce debe ser de 32 bytes", None
    if a != PAY_TO:
        return False, f"el pago no va dirigido a {PAY_TO}", None
    if valor < PRECIO_UNIDADES:
        return False, f"monto insuficiente: se requieren {PRECIO_UNIDADES} unidades (0,01 USDC)", None
    ahora = int(time.time())
    if valido_desde > ahora:
        return False, "la autorizacion todavia no es valida", None
    if valido_hasta <= ahora:
        return False, "la autorizacion ya vencio", None
    with candado:
        if nonce.hex() in nonces_usados:
            return False, "nonce ya usado (pago reutilizado)", None
    # Verificacion criptografica real: la firma debe ser de `from`.
    mensaje = {
        "types": TIPOS,
        "primaryType": "TransferWithAuthorization",
        "domain": EIP712_DOMAIN,
        "message": {
            "from": de,
            "to": a,
            "value": valor,
            "validAfter": valido_desde,
            "validBefore": valido_hasta,
            "nonce": nonce,
        },
    }
    try:
        recuperado = Account.recover_message(
            encode_typed_data(full_message=mensaje), signature=bytes.fromhex(firma[2:] if firma.startswith("0x") else firma)
        )
    except Exception:
        return False, "firma invalida", None
    if recuperado != de:
        return False, "la firma no corresponde al pagador", None
    # Chequeo opcional en cadena (no bloquea si el RPC no responde).
    en_cadena = nonce_usado_en_cadena(de, "0x" + nonce.hex())
    if en_cadena is True:
        return False, "nonce ya usado en cadena", None
    with candado:
        nonces_usados.add(nonce.hex())
    return True, {"verificado_en_cadena": en_cadena is not None}, auth


def precio_hype():
    """Precio en vivo de HYPE desde la API publica de Hyperliquid."""
    cuerpo = json.dumps({"type": "l2Book", "coin": "HYPE"}).encode()
    req = urllib.request.Request(
        HYPERLIQUID_URL, data=cuerpo, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        datos = json.loads(resp.read())
    mejor_compra = float(datos["levels"][0][0]["px"])
    mejor_venta = float(datos["levels"][1][0]["px"])
    return {
        "moneda": "HYPE",
        "precio_usd": round((mejor_compra + mejor_venta) / 2, 4),
        "bid": mejor_compra,
        "ask": mejor_venta,
        "fuente": "Hyperliquid l2Book @107",
        "ts": int(time.time()),
    }


# ---------------------------------------------------------------- servidor
class Manejador(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")

    def do_GET(self):
        recurso = f"http://{self.headers.get('Host', f'localhost:{PORT}')}{self.path}"
        if self.path == "/":
            responder(self, 200, {
                "servicio": "Niveles de HYPE en vivo, pagados con x402",
                "precio": "0,01 USDC en Base Sepolia (testnet, sin valor real)",
                "endpoint_pagado": "/niveles",
                "red": NETWORK,
                "moneda": USDC,
                "liquidacion": "SIMULADA (no se mueve plata; la firma EIP-3009 si se verifica de verdad)",
                "como_pagar": "Pedi /niveles sin pagar, recibis un 402 con los requisitos, firmas la autorizacion EIP-3009 y reintentas con el header X-PAYMENT. Mira client.py.",
            })
            return
        if self.path == "/salud":
            responder(self, 200, {"ok": True, "red": NETWORK, "precio_unidades": PRECIO_UNIDADES})
            return
        if self.path != "/niveles":
            responder(self, 404, {"error": "no existe esa ruta. Probá / o /niveles."})
            return

        header_pago = self.headers.get("X-PAYMENT")
        if not header_pago:
            responder(self, 402, requisitos_pago(recurso))
            return
        ok, detalle, auth = verificar_pago(header_pago)
        if not ok:
            cuerpo = requisitos_pago(recurso)
            cuerpo["error"] = f"Pago rechazado: {detalle}"
            responder(self, 402, cuerpo)
            return
        try:
            niveles = precio_hype()
        except Exception as exc:
            responder(self, 502, {"error": f"no pude leer el precio de Hyperliquid: {exc}"})
            return
        niveles["pago"] = {
            "red": NETWORK,
            "monto_usdc": f"{PRECIO_UNIDADES / 1_000_000:.2f}",
            "pagador": auth["from"],
            "liquidacion": "simulada",
            "nonce_verificado_en_cadena": detalle.get("verificado_en_cadena"),
        }
        respuesta_pago = base64.b64encode(json.dumps({
            "success": True,
            "payer": auth["from"],
            "transaction": "simulado-" + auth["nonce"][2:10],
            "network": NETWORK,
            "amount": str(PRECIO_UNIDADES),
            "settlement": "simulated",
        }).encode()).decode()
        responder(self, 200, niveles, {"X-PAYMENT-RESPONSE": respuesta_pago})


if __name__ == "__main__":
    print("=" * 64)
    print("Servidor demo x402 - niveles de HYPE por 0,01 USDC (testnet)")
    print(f"Escuchando en http://localhost:{PORT}  (red: {NETWORK})")
    print(f"USDC (testnet): {USDC}")
    print(f"Se cobra en:   {PAY_TO}  (demo: liquidacion SIMULADA)")
    print("Sin plata real. Ctrl+C para parar.")
    print("=" * 64)
    ThreadingHTTPServer(("0.0.0.0", PORT), Manejador).serve_forever()
