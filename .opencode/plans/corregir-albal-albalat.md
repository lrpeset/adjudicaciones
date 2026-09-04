# Plan: Corregir fallas en Quality Gate de subáreas (ALBAL / ALBALAT)

## Contexto / Fallas detectadas

1. `CEIP TIRANT LO BLANC` (ALBAL) recibe incorrectamente la subárea `4614` (debe ser `4644`).
2. `IES SUCRO` (ALBALAT DE LA RIBERA) aparece etiquetado bajo ALBAL con subárea `4652`.
   Hay un falso positivo por matching de substring ("ALBAL" coincide parcialmente con "ALBALAT DE LA RIBERA" y "ALBALAT DELS TARONGERS").

## Root Cause

En `match_coords.py` hay **dos puntos** que usan matching por **substring** (`in`) en lugar de **exact** (`==`):

### Punto A — Phase 4a `validate_critical_subareas` (línea 438)

```python
rows = df[df["Municipio"].apply(lambda x: norm_muni in _normalize_name(str(x)) if pd.notna(x) else False)]
```

Cuando `norm_muni = "albal"`, captura **todas** las filas donde Municipio contiene "albal" como substring:
- `ALBAL` → `"albal"` → match correcto
- `ALBALAT DE LA RIBERA` → `"albal" in "albalat de la ribera"` → **falso positivo**
- `ALBALAT DELS TARONGERS` → `"albal" in "albalat dels tarongers"` → **falso positivo**

Esto provoca que la quality gate falle reportando errores como:
> `ALBAL: subarea 4614 (esperada 4644) — filas de ALBALAT DELS TARONGERS`
> `ALBAL: subarea 4652 (esperada 4644) — filas de ALBALAT DE LA RIBERA (IES SUCRO)`

### Punto B — Phase 3 `MUNI_SUBAREA_OVERRIDE` (líneas 324-326)

```python
if keyword in muni_norm:
    result.at[idx, "Zona_Subarea"] = str(code).strip()
```

Si una fila de ALBALAT tuviera `Zona_Subarea` vacía, el keyword "albal" podría sobre-escribir su subarea correcta con `4644`.

## Mapeos correctos en zonas.json (Source of Truth)

- `ALBAL` → `4644`
- `ALBALAT DE LA RIBERA` → `4652`
- `ALBALAT DELS TARONGERS` → `4614`

## Cambios propuestos

### Cambio 1 — `match_coords.py:438` (Phase 4a)

Cambiar de substring a exact match:

```python
# ANTES:
rows = df[df["Municipio"].apply(lambda x: norm_muni in _normalize_name(str(x)) if pd.notna(x) else False)]
# DESPUÉS:
rows = df[df["Municipio"].apply(lambda x: _normalize_name(str(x)) == norm_muni if pd.notna(x) else False)]
```

### Cambio 2 — `match_coords.py:324-326` (Phase 3)

Cambiar de substring a exact match:

```python
# ANTES:
if keyword in muni_norm:
    result.at[idx, "Zona_Subarea"] = str(code).strip()
# DESPUÉS:
if muni_norm == keyword:
    result.at[idx, "Zona_Subarea"] = str(code).strip()
```

**Nota:** `SELF_HEAL_MAP` (línea 430) se **mantiene** con substring porque debe cubrir pedanías como "ELX - ALTABIX" → "elx". El test existente `test_self_heals_empty_elx_pedanias_to_0351` depende de ello.

### Cambio 3 — `test_match_coords.py` (tests de regresión)

Añadir tests en la clase `TestValidateCriticalSubareas`:

1. **Test de positive case**: DF con ALBAL (4644), ALBALAT DE LA RIBERA (4652), ALBALAT DELS TARONGERS (4614) → la quality gate pasa sin errores.
2. **Test de negative case**: ALBAL con subarea incorrecta (9999) lanza AssertionError con "4644", pero las filas de ALBALAT con subareas correctas NO se contabilizan como error.
3. **Test de integración** con `inject_coordinates` (zonas.json real):
   - `ALBAL` → `4644`
   - `ALBALAT DE LA RIBERA` → `4652`
   - `ALBALAT DELS TARONGERS` → `4614`

## Archivos a modificar

| Archivo | Cambios |
|---|---|
| `match_coords.py:438` | Phase 4a: substring → exact match |
| `match_coords.py:324-326` | Phase 3: substring → exact match |
| `test_match_coords.py` | +3 tests de regresión |

## Impacto en tests existentes

Los 166 tests existentes deben pasar sin cambios porque:
- Los tests de `validate_critical_subareas` usan nombres exactos ("Aldaia", "Albal", "Cofrentes").
- El test de self-heal de ELX pedanías usa `SELF_HEAL_MAP` (se mantiene con substring).
- El test de ALMORADÍ suffix usa nombre exacto.

## Verificación final

Ejecutar la suite completa:

```bash
python -m pytest -q
```

Esperado: 166 tests previos + 3 nuevos = 169 tests pass.
