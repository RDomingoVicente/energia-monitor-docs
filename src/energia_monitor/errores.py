"""Excepciones del colector."""


class ErrorFuente(Exception):
    """Fallo al obtener o interpretar los datos de una fuente."""


class ErrorAutenticacion(ErrorFuente):
    """La fuente rechaza las credenciales (HTTP 401 o 403): no se reintenta."""
