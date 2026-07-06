"""Interruptores de configuración / beta, compartidos por main.py y los routers.
Tocar aquí afecta a toda la app."""
import os

# Sondeo de partidas: cada cuántos segundos el poller consulta la API (lo reporta
# también /api/status). Configurable por entorno.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "180"))

# Para abrir el registro libre: REGISTRATION_OPEN = True (activa el endpoint de
# registro y el botón "Crear cuenta" deja de estar gris).
REGISTRATION_OPEN = False

# Apertura CONTROLADA para la beta ampliada: aunque REGISTRATION_OPEN sea False, si
# se define una "contraseña de verja" (REGISTRATION_GATE_CODE en el entorno) el botón
# "Creación de Cuenta" del portal muestra el formulario a quien la conozca. Las cuentas
# creadas así quedan PENDIENTES hasta que un administrador las aprueba (o se quedan
# inactivas). Es temporal: cuando se abra del todo, poner REGISTRATION_OPEN = True.
REGISTRATION_GATE_CODE = os.environ.get("REGISTRATION_GATE_CODE", "")
# Los registros nuevos requieren aprobación de un admin antes de poder entrar.
REGISTRATION_REQUIRES_APPROVAL = True

# Para limitar el gasto de informes de IA por usuario cuando abras la beta:
REPORT_QUOTA_ENABLED = False
MONTHLY_REPORT_LIMIT = 12  # informes por usuario y mes (cuando la cuota esté activa)
