# Carga las variables de .env (si existe) antes de que el resto de módulos
# lean os.environ. Así basta con rellenar .env y arrancar.
import os
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass
