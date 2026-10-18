# Armado de muros — Insurgentes SD

Excel para el botón de pyRevit "Armado de muros" (hoja **Armado**), leído del plano `Muros_I121.dwg`.

## Niveles

- Detalles **N1-N7**: muros que arrancan en N1…N6 y terminan en el lecho inferior de la losa N7 → `N1-N6`.
- Detalles **N7-ROOF**: muros que arrancan en N7, N8 y N9 → `N7-N9`.
- Detalles **N1-ROOF** (M-1, M-2, M-4, M-16): un solo armado `N1-N9`.
- Cortes intermedios del plano: N1-N5 → `N1-N4` + `N5-N6`; N1-N3 → `N1-N2` + `N3-N6`;
  M-5: `N7-N8` (N7-N9) y `N9` (N9-ROOF).
- Roof garden = N10, +30.55.

## Decisiones confirmadas por el usuario

- **M-20**: el segundo detalle rotulado "N5 A N7" es N7-ROOF (error de dibujo) → N7-N9 `4#6+16#4 80 2E@15`.
- **M-23**: 1 estribo + 1 grapa en ambos extremos, en todos los niveles.
- **M-9**: Lb de la intersección = 60 cm en todos los niveles (el 45 del plano N1-N7 era error).
- **Cimentación**: ya está armada; no va en este Excel.

## Pendiente

- **Azotea ("ROOF - TECHOS")**: aún no está modelada. Los datos leídos del DWG están en la hoja
  "Azotea (pendiente)" del Excel y en `azotea_roof_techos.json` (sin asignar a extremos).
  Muros: M-1, M-8, M-10, M-11, M-12, M-13, M-14, M-15, M-28, M-29, M-30, M-31/M-31A, M-32, M-33/M-33A,
  M-34, M-35, M-36. Falta el nombre del nivel de azotea en el modelo.
- Correr **Revisar** con el Excel en el modelo.
