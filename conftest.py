"""Configuración global de pruebas.

pytest-socket bloquea cualquier acceso de red real. Las pruebas de OSRM usan
mocks explícitos sobre urllib.request.urlopen.
"""
