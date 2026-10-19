# Armado de trabes — Roof Garden (N10) — Insurgentes SD

Excel del botón "Armado Trabes Excel" llenado desde `Trabes_Roof_N10.dwg` (alzados de trabes, capas ARM-TR / NOTAS /
VARILLA TRABE / H_COLUMNA / COTAS-TR).

- Corridos, estribos y bastones por tramo; "Estribo sobre muro" vacío = @20 de Configuracion (el plano marca @20 sobre muros).
- Tramos con varios tramos de Revit dentro de un claro del plano: `E#3 11@10 @20 -` / `E#3 @20` / `E#3 - @20 11@10`.
- Bastones de extremo: longitudes hasta el extremo de la trabe (`255/60`) o `-30` en Ref. centro (se recortan al extremo).
- La columna Notas de cada tipo dice de qué trabe del plano sale y lo que hay que revisar (tipos de Revit con varias
  vigas que en el plano tienen armado distinto: EJE-10, EJE-I, T-10, T-15, T-20 S2, T-11 S2...).
- En el plano hay una TRABE T-7 (ejes K-I, 3#4, 11@10 @20 11@10) que no está en el modelo (no hay tipo "T-7").
