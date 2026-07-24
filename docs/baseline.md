# Baseline reproducible

Esta fase caracteriza el estado actual del proyecto. Las cifras registradas no
son objetivos de calidad: son un contrato de detección de cambios para que las
correcciones posteriores sean explícitas y revisables.

## Versiones de Python

- Mínima soportada: Python 3.10.
- Recomendada: Python 3.12.
- El lock se resuelve para Python 3.10 y se valida en CI con 3.10 y 3.12.

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
# Suite rápida; no abre sockets y no procesa las 742 páginas
.\.venv\Scripts\python -m pytest -m "not full_pdf"

# Baseline completa del PDF
.\.venv\Scripts\python -m pytest -m full_pdf

# Análisis estático de errores de alta señal
.\.venv\Scripts\python -m ruff check .

# Generación explícita de informes y comparación
.\.venv\Scripts\python scripts/generate_pdf_baseline.py --check

# Aplicación
.\.venv\Scripts\python -m streamlit run app.py
```

Las pruebas de rutas parchean `urllib.request.urlopen`. `pytest-socket` bloquea
además cualquier conexión real, por lo que OSRM no se consulta durante CI.

## Contrato del PDF

Entrada:

- `lis_vac_adj_ini_26_27.pdf`
- SHA-256:
  `A71B67FB9C89D22D32CA2D6C7A6539B5E35261CBCC761F72D1B8FE1C210D5567`
- 3.669.846 bytes.
- 742 páginas.
- Índices fuente continuos del 1 al 14.141.

Baseline del parser y enriquecimiento:

| Métrica | Valor |
|---|---:|
| Filas parseadas | 13.506 |
| Índices ausentes | 635 |
| Índices duplicados | 0 |
| Filas sin `Lloc` | 1.333 |
| Centros incompletos | 7 |
| Subáreas no resueltas | 134 |
| Coordenadas no resueltas | 784 |

El contrato versionado está en
`tests/baselines/lis_vac_adj_ini_26_27.json`.

## Informes estructurados

La ejecución completa escribe en `artifacts/baseline/`:

- `summary.json`
- `missing_indices.csv`
- `missing_lloc.csv`
- `incomplete_centers.csv`
- `unresolved_subareas.csv`
- `unresolved_coordinates.csv`

El directorio es local y está ignorado por Git; CI lo publica como artefacto.
Los CSV se ordenan por `Índex` y se escriben en UTF-8.

## Defectos conocidos

Se mantienen como `xfail(strict=True)`:

- el quality gate no rechaza subáreas erróneas para ALBAL, ALDAIA y COFRENTES;
- el parser no recompone el fixture multilínea;
- `generar_bloques()` no invoca `validate_block_sorting()`.

Un arreglo futuro producirá un `XPASS` y obligará a retirar el marcador en el
mismo cambio.

## Actualizar la baseline

No se debe editar el JSON esperado para hacer verde CI sin investigar antes el
informe detallado. Cuando un cambio funcional autorizado altere las métricas:

1. ejecutar la baseline completa;
2. revisar todos los CSV y confirmar que el cambio es el esperado;
3. actualizar `tests/baselines/lis_vac_adj_ini_26_27.json`;
4. explicar en el commit qué métricas cambian y por qué.

## Actualizar dependencias

Los locks se regeneran deliberadamente apuntando a Python 3.10:

```powershell
py -3.12 -m pip install "pip-tools>=7.4.0"
py -3.12 -m piptools compile --strip-extras --generate-hashes --pip-args="--python-version 3.10 --only-binary=:all:" --output-file requirements.txt pyproject.toml
py -3.12 -m piptools compile --strip-extras --allow-unsafe --extra dev --generate-hashes --pip-args="--python-version 3.10 --only-binary=:all:" --output-file requirements-dev.txt pyproject.toml
```

Después se repiten instalación limpia, suite rápida, Ruff y baseline completa.
