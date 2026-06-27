"""Interruptores de configuración / beta, compartidos por main.py y los routers.
Tocar aquí afecta a toda la app."""
import os

# Sondeo de partidas: cada cuántos segundos el poller consulta la API (lo reporta
# también /api/status). Configurable por entorno.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "180"))

# Para abrir el registro libre: REGISTRATION_OPEN = True (activa el endpoint de
# registro y el botón "Crear cuenta" deja de estar gris).
REGISTRATION_OPEN = False

# Para limitar el gasto de informes de IA por usuario cuando abras la beta:
REPORT_QUOTA_ENABLED = False
MONTHLY_REPORT_LIMIT = 12  # informes por usuario y mes (cuando la cuota esté activa)
