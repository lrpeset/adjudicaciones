# Baseline reproducible

Esta fase caracteriza el estado actual del proyecto. Las cifras registradas no
son objetivos de calidad: son un contrato de detección de cambios para que las
correcciones posteriores sean explícitas y revisables.

## Versiones de Python

- Mínima soportada: Python 3.10.
- Recomendada: Python 3.12.
- El lock se resuelve para Python 3.10 y se valida en CI con 3.10 y 3.12.
- `pandas`, `numpy` y `rpds-py` tienen límites superiores explícitos porque sus
  versiones más recientes ya no son compatibles con Python 3.10.

## Instalación

En PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install --require-hashes -r requirements-dev.txt
```

Para ejecutar únicamente la aplicación se puede instalar `requirements.txt`.

## Comandos oficiales

```powershell
# Suite de pruebas; no abre sockets
.\.venv\Scripts\python -m pytest

# Análisis estático de errores de alta señal
.\.venv\Scripts\python -m ruff check .

# Aplicación
.\.venv\Scripts\python -m streamlit run app.py
```

Las pruebas de rutas parchean `urllib.request.urlopen`. `pytest-socket` bloquea
además cualquier conexión real, por lo que OSRM no se consulta durante CI.

## Defectos conocidos

Se mantienen como `xfail(strict=True)`:

- el parser no recompone el fixture multilínea;

Un arreglo futuro producirá un `XPASS` y obligará a retirar el marcador en el
mismo cambio.

> Nota: el quality gate de subáreas críticas (ALBAL, ALDAIA, COFRENTES) y la
> invocación de `validate_block_sorting()` desde `generar_bloques()` ya fueron
> corregidos, por lo que estos dos `xfail` se retiraron.

## Actualizar dependencias

Los locks se regeneran con un intérprete Python 3.10 real. No basta con pasar
`--python-version 3.10` a `pip`, porque `pip-compile` evalúa las dependencias
condicionales con la versión del intérprete que lo ejecuta:

```powershell
py -3.10 -m pip install "pip-tools>=7.4.0" typing-extensions
py -3.10 -m piptools compile --strip-extras --generate-hashes --pip-args="--only-binary=:all:" --output-file requirements.txt pyproject.toml
py -3.10 -m piptools compile --strip-extras --allow-unsafe --extra dev --generate-hashes --pip-args="--only-binary=:all:" --output-file requirements-dev.txt pyproject.toml
```

Después se repiten instalación limpia, suite de pruebas y Ruff.
Antes de publicar el lock se valida también su resolución completa para 3.10:

```powershell
py -3.10 -m pip install --dry-run --ignore-installed --require-hashes --only-binary=:all: -r requirements-dev.txt
```
