# Armado de trabes — Roof Garden (N10) — Insurgentes SD

Excel del botón "Armado Trabes Excel" llenado desde `Trabes_Roof_N10.dwg` (alzados de trabes, capas ARM-TR / NOTAS /
VARILLA TRABE / H_COLUMNA / COTAS-TR).

- Corridos, estribos y bastones por tramo; "Estribo sobre muro" vacío = @20 de Configuracion (el plano marca @20 sobre muros).
- Tramos con varios tramos de Revit dentro de un claro del plano: `E#3 11@10 @20 -` / `E#3 @20` / `E#3 - @20 11@10`.
- Bastones de extremo: longitudes hasta el extremo de la trabe (`255/60`) o `-30` en Ref. centro (se recortan al extremo).
- La columna Notas de cada tipo dice de qué trabe del plano sale y lo que hay que revisar (tipos de Revit con varias
  vigas que en el plano tienen armado distinto: EJE-10, EJE-I, T-10, T-15, T-20 S2, T-11 S2...).
- En el plano hay una TRABE T-7 (ejes K-I, 3#4, 11@10 @20 11@10) que no está en el modelo (no hay tipo "T-7").

## Versión 2 (plantilla por trabe del plano)

- Plantilla generada con el botón actualizado: cada trabe del plano con todas sus secciones juntas y una
  sección de filas por viga separada ("EJE-10 (viga 1)", "(viga 2)"); cada viga con el armado de su tramo en el plano.
- Piel `#3@30` en todas (una varilla por cara a cada 30 cm; diámetro supuesto #3).
- T-6 (viga 2): `E#3 11@10,5@15 @20`.
- Tramos que el plano no detalla (EJE-5 t4-t5, T-5 viga 2 t5-t6, T-6 viga 2 t2-t3): supuestos, ver Notas.

## Techos (N11)

- `Armado_trabes_Nivel_Techos_N11.xlsx` desde `Trabes_Techos_N11.dwg`, mismo criterio (piel `#3@30`).
- Tramos largos del modelo que abarcan varios claros del plano (T-1 t2, T-2 t1, T-6 t1, EJE 8 t1, EJE J t2):
  estribos escritos grupo por grupo desde cada apoyo para reproducir los del plano.
- El refuerzo "al centro" se recorta a los apoyos del tramo: las varillas que pasan el apoyo vecino van como
  bastón en el apoyo (Roof EJE-11 t4, Techos T-EJE K t3).

## Secciones separadas en el plano

El botón une las secciones de una trabe que se tocan en el modelo. Donde el plano las dibuja como vigas
separadas se arman por partes con corchetes: `T-5 (viga 2) [S1, S2]` / `[S3]`, `T-6 (viga 2) [S2]` / `[S3]`,
`EJE-5 [S1]` / `[S2]` (Roof) y `T- EJE 3 [S2]` / `[S1]` (Techos). Siguen unidas (continuas en el plano):
EJE-B, EJE-1', EJE-2, T-11.
