# Demo x402: niveles de HYPE pagados con centavos

Un mini-servicio que cobra **0,01 USDC de prueba** por darte el precio de HYPE
en vivo. Es la estructura mínima de lo que CZ predice: un agente de IA que
paga solo, con cripto, por un dato que necesita.

**Sin plata real.** Todo corre en Base Sepolia (red de pruebas). El USDC de
prueba no vale nada.

## Qué hace

- `GET /niveles` sin pagar → el servidor responde **402** con lo que hay que pagar.
- El cliente (el "agente") firma una autorización y reintenta con el pago →
  recibe el precio de HYPE en vivo desde Hyperliquid.

## Qué necesitás (en tu PC con Windows)

1. **Python 3.10 o más nuevo** (python.org, marcá la casilla "Add Python to PATH").
2. Nada más. No hace falta faucet ni billetera con fondos para probar la demo,
   porque la liquidación es simulada (ver abajo).

## Cómo correrlo

En PowerShell, dentro de la carpeta `x402-demo`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

Dejá esa ventana abierta. En **otra** ventana de PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python client.py
```

Vas a ver al agente pedir los niveles, recibir el 402, firmar el pago y
recibir el precio. Con `python client.py --mostrar-pago` te muestra el pago
firmado para reintentarlo vos mismo con curl.

Para parar el servidor: `Ctrl+C` en su ventana.

## Qué es real y qué es simulado

| Parte | Estado |
|---|---|
| Respuesta 402 con los requisitos de pago (protocolo x402, esquema `exact`) | ✅ Real |
| Firma EIP-3009 (`transferWithAuthorization`) y verificación EIP-712 | ✅ Real (criptografía de verdad) |
| Precio de HYPE en vivo desde la API pública de Hyperliquid | ✅ Real |
| Protección contra reutilizar un pago (nonce usado se rechaza) | ✅ Real |
| Dirección de USDC en Base Sepolia (`...CF7e`, verificada con Circle) | ✅ Real |
| Chequeo del nonce en cadena por RPC público | ⚠️ Se intenta, pero en esta VM el RPC no respondió a tiempo; el servidor sigue solo con la verificación local y lo avisa en la respuesta |
| **Liquidación on-chain** (mover los USDC de verdad) | ❌ **Simulada**: no se transmite ninguna transacción. El servidor solo marca el nonce como usado en memoria |
| La billetera del cliente | Temporal: se crea en memoria en cada corrida y se olvida. Nunca se guarda nada en disco |

## Qué falta para que sea de verdad

1. **Liquidar en cadena**: llamar a `transferWithAuthorization` en el contrato
   USDC con una billetera que tenga ETH de Sepolia para el gas. Hoy el
   servidor no tiene clave privada (a propósito).
2. **Faucet**: para probar la liquidación real necesitás USDC de prueba en
   https://faucet.circle.com (elegís Base Sepolia) y ETH de Sepolia para el
   gas en un faucet de Base.
3. **Un facilitador** (como el de Coinbase) o una verificación de saldo
   on-chain, para no fiarse solo de la firma.
4. **HTTPS y un dominio** si lo querés público, y un `payTo` con tu billetera
   de verdad (hoy es la dirección `0x...dEaD`, que es de quemar: a propósito,
   para que nadie mande plata real por error).
5. **Persistir los nonces** (hoy se olvidan al reiniciar el servidor).

## Seguridad: leelo

- **Nunca guardes claves ni semillas en archivos.** Este demo no tiene ninguna.
- La billetera del cliente es temporal y se crea en cada corrida.
- No uses este código con plata real sin hacer los 5 puntos de arriba y sin
  entender cada paso: un agente con una clave privada y sin límites puede
  vaciar una billetera en segundos.
- El precio es de prueba (0,01 USDC), pero el protocolo es el mismo que en
  producción: si cambiás la red a Base mainnet sin querer, es plata real.
