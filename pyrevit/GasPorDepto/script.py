# -*- coding: utf-8 -*-
"""
ASIGNACION DE INSTALACION DE GAS POR DEPARTAMENTO
=================================================
Version 2.0  -  Ventana no modal (modeless) + ExternalEvent.
               Areas de modelos vinculados + nivel rector desde "Area Cuantif".

QUE HACE
--------
Recorre la red de gas y escribe en cada elemento (tuberia, conexion, valvula,
medidor, flexible, aparato) el departamento al que pertenece, para poder
cuantificar por depto.

POR QUE NO SE PARECE AL SCRIPT DE HIDRAULICA
--------------------------------------------
El script de hidraulica decide por POSICION: mira donde cae cada tramo y vota
que Area lo contiene. Eso funciona en hidraulica porque el montante es comun y
los ramales corren dentro del propio depto.

En gas la topologia es la contraria y ese criterio se rompe:

  * Los 110 medidores estan TODOS en azotea, en 19 bancos. Sobre la azotea las
    Areas que existen son RG 1..13, B 01..48, GAS, BAJANTES, Circulacion. Por
    eso la corrida anterior dejo 184 elementos en "B 29", 317 en "B 22",
    325 en "B 43", 63 en "RG 8", etc.: son etiquetas de bodega y roof garden,
    no de departamento.

  * De cada medidor baja un ramal DEDICADO a un solo depto. Ese tramo es
    vertical y cruza pisos, asi que el script de hidraulica lo marcaba
    "Ducto vertical" (525 elementos) y tiraba a la basura metros lineales que
    SI son de un departamento.

  * Resultado de la corrida anterior sobre gas: 1466 elementos en "Sin asignar"
    y 295 en "GAS", de 7800.

LA IDEA DE ESTE SCRIPT: CORTAR EN EL MEDIDOR
--------------------------------------------
El medidor es, por definicion contractual, la frontera entre lo comun y lo
privativo. Asi que no hace falta adivinar nada por posicion:

  1. Se arma el grafo de la red con los conectores de Revit.
  2. Se QUITAN los medidores del grafo. La red se parte sola.
  3. Cada pedazo que toca UN solo medidor = ramal de ese depto (bajada
     incluida, completa, desde azotea hasta la estufa).
  4. El pedazo que toca el tanque, o que toca VARIOS medidores, = red general.
  5. Solo entonces se usa la posicion, y solo para PREGUNTAR una cosa: de los
     elementos de este ramal, cuales caen dentro de un Area de departamento.
     Se vota y gana la mayoria.

La posicion deja de decidir el resultado y pasa a ser solo la etiqueta. Por eso
la bajada ya no se pierde: hereda el depto del ramal al que pertenece, aunque
ella misma cruce seis losas.

DETALLE QUE VALE ORO: BALCON Y TERRAZA SON DEL DEPTO
-----------------------------------------------------
El calentador vive en el balcon. En el modelo el balcon es un Area aparte
("Balcon 613", "Terraza 112"). Si se vota crudo, medio ramal vota "Dpto. 613"
y la otra mitad "Balcon 613", y el resultado se parte en dos partidas.
Aqui "Balcon NNN" y "Terraza NNN" se normalizan a "Dpto. NNN" antes de votar.

VALIDACION QUE NO TENIA EL DE HIDRAULICA
-----------------------------------------
Cada banco alimenta una COLUMNA de deptos (p. ej. 113, 213, 313, 413, 513, 613:
mismos dos ultimos digitos, un piso cada uno). El script verifica eso solo:
si un banco resuelve 113/213/313/413/513 y el sexto sale "Dpto. 512", lo marca
como sospechoso. Y si un medidor se queda sin ramal, te dice exactamente que
piso falta en esa columna.

NOVEDADES v2.0: PROYECTOS VINCULADOS Y NIVEL RECTOR
--------------------------------------------------
1. AREAS DE LOS VINCULOS. El modelo de instalaciones normalmente NO tiene
   Areas: viven en el modelo de arquitectura, que entra como vinculo. Ahora el
   script lee las Areas que tiene PROPIAMENTE cada vinculo cargado (con su
   transformacion, asi que da igual si se posiciono por origen o por
   coordenadas compartidas), y si el modelo activo tiene Areas propias tambien
   las lee. Las dos fuentes se pueden prender y apagar desde la ventana, y los
   vinculos se pueden filtrar por nombre.

   Lo que se ESCRIBE sigue siendo la red del modelo activo: los elementos de
   un vinculo son de solo lectura desde el anfitrion, Revit no deja tocarlos.

2. NIVEL RECTOR = "Area Cuantif". Antes el nivel de cada elemento se deducia
   de su Z y "Area Cuantif" solo servia de testigo. Ahora es al reves: el
   nivel que dice "Area Cuantif" es el que MANDA, y solo se prueban las Areas
   de ese nivel. La Z queda como respaldo para los elementos que no tienen el
   parametro lleno, o cuyo valor no corresponde a ningun nivel (se reportan).

   Como los niveles del vinculo y los del modelo activo son elementos
   distintos, las Areas se agrupan por NOMBRE de nivel ("Nivel 05" = "nivel 5").
   Si un nivel del vinculo se llama distinto, se empata por elevacion con el
   nivel del modelo activo que tenga el nombre que trae "Area Cuantif".

USO
---
  Boton         -> abre la ventana. Queda abierta, puedes seguir usando Revit.
  Diagnosticar  -> calcula TODO y te muestra el reporte SIN tocar el modelo.
  Asignar       -> lo mismo, pero escribe el parametro.
  SHIFT + clic  -> reabre la configuracion (nombre de parametro, tolerancia...).

Requiere pyRevit. Probado contra Revit 2024 / 2026.
"""

__title__ = "Gas\npor Depto"
__doc__ = ("Asigna el departamento a toda la red de gas cortando en el medidor. "
           "Lee Areas del modelo activo y de los vinculos; el nivel lo manda "
           "el parametro 'Area Cuantif'.")
__author__ = "Osvaldo"

# Mantiene vivo el motor de IronPython despues de que el script termina.
# Sin esto la ventana no modal se congela en cuanto acaba la ejecucion.
__persistentengine__ = True

import math
import re
import unicodedata
from collections import deque

from pyrevit import revit, DB, forms, script

from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from System.Collections.Generic import List
from Autodesk.Revit.Exceptions import InvalidOperationException

logger = script.get_logger()
output = script.get_output()
cfg = script.get_config()


# =============================================================================
#  CONFIGURACION
# =============================================================================

VALOR_COMUN      = "Red general gas"
VALOR_SIN        = "Sin asignar"
VALOR_DUPLICADO  = "DUPLICADO - revisar"

FT = 0.3048  # metros por pie

# Palabras que identifican un medidor, en el nombre de familia o de tipo.
# En este modelo: familia "Generic-Gas_Meter", tipo "Medidor Kumho KG-2 de 10 m3/h".
CLAVES_MEDIDOR_DEF = "medidor, gas_meter, gasmeter, contador"

# Palabras que VETAN un match de medidor aunque coincida. Medido en el modelo:
# la familia "Safety_relief_valve-Caleffi-531" tiene un tipo llamado "Medidor
# Indica...", y colarse como medidor cortaria la red donde no debe.
CLAVES_VETO_DEF = "valve, valvula, relief, safety, manometro, gauge"

# Palabras que identifican el tanque / recipiente / regulador de la acometida.
# En este modelo: "CHEMET - 6450L - PROPANE TANK 7 CBM, ON GROUND".
CLAVES_TANQUE_DEF = "tank, tanque, propane, recipiente, regulador, regulator"

# Prefijos de Area que SI pertenecen a un departamento. "Balcon 613" y
# "Terraza 112" cuentan como su depto porque ahi vive el calentador.
PREFIJOS_DEPTO = ["dpto", "depto", "departamento", "balcon", "terraza"]

# Prefijo con el que se escribe el resultado. "Dpto. 613".
FORMATO_DEPTO = "Dpto. {}"

# Categorias que forman la red. Si algun elemento sale con el parametro en
# blanco, lo mas probable es que su categoria falte aqui: agregala y listo.
# (El script reporta el conteo por categoria justo para que se note.)
def _cats_red():
    c = [
        DB.BuiltInCategory.OST_PipeCurves,          # Tuberias
        DB.BuiltInCategory.OST_PipeFitting,         # Conexiones
        DB.BuiltInCategory.OST_FlexPipeCurves,      # Tuberia flexible
        DB.BuiltInCategory.OST_PipeAccessory,       # Accesorios: valvulas, medidores, tanque
        DB.BuiltInCategory.OST_PlumbingFixtures,    # Aparatos: llaves, valvulas angulares
        DB.BuiltInCategory.OST_MechanicalEquipment, # Equipo mecanico
        DB.BuiltInCategory.OST_SpecialityEquipment, # Equipo especializado
    ]
    # OST_PlumbingEquipment solo existe de Revit 2023 en adelante.
    try:
        c.append(DB.BuiltInCategory.OST_PlumbingEquipment)
    except Exception:
        pass
    return c

CATS_RED = _cats_red()

# Un tramo se considera "vertical" si su angulo con Z es menor a esto. Aqui NO
# sirve para descartar nada -en gas la bajada es del depto-, solo para el
# reporte y para el desempate por altura.
ANGULO_VERTICAL_GRADOS = 30.0
UMBRAL_COSENO = abs(math.cos(math.radians(90 - ANGULO_VERTICAL_GRADOS)))

# CAMBIO CLAVE v1.3 - EL FALSO POSITIVO DE NIVEL (lo cazo Osvaldo en obra).
#
# Un elemento pertenece al nivel SOBRE EL QUE SE APOYA. Para decidirlo se mira
# cual es el nivel mas alto que queda por debajo de su Z, con una tolerancia
# que absorbe el ruido de modelado.
#
# El script de hidraulica usa 0.15 m. Ahi tenia sentido: cubria el tubo
# embebido en la losa, apenas por debajo del piso terminado, y el caso
# problematico de aquel modelo estaba a 300 mm por debajo, muy fuera de la
# tolerancia.
#
# EN GAS NO. La tuberia de gas no corre por el piso: corre por el TECHO. Un
# tubo un par de centimetros por debajo de una losa no esta embebido en ella:
# esta colgado del plafon del departamento de ABAJO.
#
# Medido en Serapio Rendon 15, tubo id 13753007:
#     Z del tubo        : 54.554 ft
#     losa de Nivel 6   : 54.626 ft  ->  el tubo queda  21.8 mm POR DEBAJO
#     piso de Nivel 5   : 45.276 ft  ->  el tubo queda 2828   mm por ENCIMA
# 2828 mm es exactamente altura de plafon: es tuberia de un depto 5xx. Con
# 0.15 m se iba a Nivel 6 y salia "Dpto. 619". Con 0.005 m sale "Dpto. 519",
# que es lo correcto y lo que dice el propio parametro "Area Cuantif".
#
# Reparto de los 3672 tubos de gas respecto a la losa que tienen encima:
#      0-  5 mm :   77
#      5- 25 mm :  521   <- la franja del problema
#     25- 50 mm :  379
#     50-150 mm :  186
#    > 150   mm : 2509
# Con 0.15 m, 1163 tubos se iban al piso de arriba. Con 0.005 m solo se
# quedan arriba los 77 que estan practicamente en la cota del nivel, que es
# justo el ruido de modelado que la tolerancia debe absorber.
#
# Si alguna vez reusas esto en una instalacion que SI corre por el piso
# (hidraulica, sanitaria), sube este valor a 0.15 m.
TOL_NIVEL_INFERIOR_M = 0.005

# Parametro de cada elemento que trae su NIVEL RECTOR ("Nivel 5", "Nivel 6"...).
# Si esta lleno y corresponde a un nivel, manda sobre la Z. Vacio en la ventana
# = volver al comportamiento anterior (nivel por Z para todo).
PARAM_NIVEL_DEF = "Area Cuantif"

# Para empatar un nivel del vinculo con uno del modelo activo cuando NO se
# llaman igual: si sus elevaciones (ya en coordenadas del anfitrion) quedan a
# menos de esto, son el mismo piso.
TOL_ELEV_VINCULO_M = 0.05

# Radio para agrupar medidores en un mismo banco (solo para el reporte y la
# validacion de serie). Los medidores de un banco estan a ~0.25 m entre si y
# los bancos estan a decenas de metros: 1.5 m separa con holgura.
RADIO_BANCO_M = 1.5


# =============================================================================
#  UTILIDADES
# =============================================================================

def eid_int(element_id):
    """Entero de un ElementId, en 2024+ (Value) y en versiones viejas."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def normalizar(texto):
    """Minusculas y sin acentos, para comparar nombres sin sustos."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def lista_claves(texto):
    return [normalizar(t) for t in (texto or "").split(",") if normalizar(t)]


def clave_nivel(texto):
    """Clave para comparar nombres de nivel entre el modelo activo, los
    vinculos y el parametro "Area Cuantif".

    "Nivel 05" -> "nivel 5"     "NIVEL  5" -> "nivel 5"     "Nivel 5." -> "nivel 5"
    """
    k = normalizar(texto)
    if not k:
        return ""
    k = re.sub(r"\s+", " ", k).strip(" .")
    return re.sub(r"(?<!\d)0+(\d)", r"\1", k)


def leer_texto_parametro(e, nombre):
    """Valor de texto de un parametro de ejemplar, o de tipo si no esta en el
    ejemplar. Cadena vacia si no existe o esta en blanco."""
    if e is None or not nombre:
        return ""
    fuentes = [e]
    try:
        t = e.Document.GetElement(e.GetTypeId())
        if t is not None:
            fuentes.append(t)
    except Exception:
        pass
    for f in fuentes:
        try:
            p = f.LookupParameter(nombre)
        except Exception:
            p = None
        if p is None:
            continue
        try:
            v = p.AsString() or p.AsValueString() or ""
        except Exception:
            v = ""
        v = v.strip()
        if v:
            return v
    return ""


_RE_DEPTO = re.compile(r"^(?:%s)\.?\s*[#-]?\s*(\d{2,4})\s*$" % "|".join(PREFIJOS_DEPTO))


def area_a_depto(nombre_area):
    """Convierte el nombre de un Area al departamento que le corresponde.

    "Dpto. 613"   -> "Dpto. 613"
    "Balcon 613"  -> "Dpto. 613"   (el calentador vive ahi, es del mismo depto)
    "Terraza 112" -> "Dpto. 112"
    "RG 8"        -> None          (roof garden, area comun)
    "B 29"        -> None          (bodega)
    "GAS"         -> None
    "Circulacion" -> None
    """
    m = _RE_DEPTO.match(normalizar(nombre_area))
    if not m:
        return None
    return FORMATO_DEPTO.format(m.group(1))


def numero_de_depto(valor_depto):
    """De "Dpto. 613" saca (6, "13"): piso y columna. Sirve para validar series."""
    m = re.search(r"(\d{2,4})\s*$", valor_depto or "")
    if not m:
        return None, None
    n = m.group(1)
    if len(n) < 3:
        return None, n
    return n[:-2], n[-2:]


def get_conectores(e):
    """Conectores MEP de cualquier elemento de red, sea MEPCurve o FamilyInstance."""
    try:
        if isinstance(e, DB.MEPCurve):
            cm = e.ConnectorManager
            if cm:
                return list(cm.Connectors)
        elif isinstance(e, DB.FamilyInstance):
            mep = e.MEPModel
            if mep and mep.ConnectorManager:
                return list(mep.ConnectorManager.Connectors)
    except Exception as err:
        logger.debug("Sin conectores en {}: {}".format(eid_int(e.Id), err))
    return []


def nombre_familia(e):
    try:
        if isinstance(e, DB.FamilyInstance):
            return e.Symbol.FamilyName or ""
    except Exception:
        pass
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""


def nombre_elemento(e):
    """Name de un elemento. En IronPython el acceso directo a .Name a veces
    choca con la propiedad estatica, por eso el segundo intento."""
    if e is None:
        return ""
    try:
        return e.Name or ""
    except Exception:
        pass
    try:
        return DB.Element.Name.GetValue(e) or ""
    except Exception:
        return ""


def nombre_tipo(e):
    try:
        t = e.Document.GetElement(e.GetTypeId())
        if t:
            return nombre_elemento(t)
    except Exception:
        pass
    return ""


def coincide_claves(e, claves):
    """True si el nombre de familia o de tipo contiene alguna de las claves."""
    if not claves:
        return False
    fam = normalizar(nombre_familia(e))
    tip = normalizar(nombre_tipo(e))
    for k in claves:
        if k and (k in fam or k in tip):
            return True
    return False


# =============================================================================
#  1. MOTOR GEOMETRICO: NIVELES Y AREAS
# =============================================================================
# El nivel RECTOR de cada elemento sale del parametro "Area Cuantif", que
# Osvaldo mantiene a mano. NO se usa el parametro de nivel de Revit (que en
# este modelo miente: un medidor declara "Nivel Techos" y esta 2.41 m por
# debajo).
#
# Para los elementos sin "Area Cuantif" se conserva el respaldo del script de
# hidraulica, ya depurado contra el modelo: el nivel se saca de su Z REAL, y un
# elemento pertenece al nivel SOBRE EL QUE SE APOYA, no al mas cercano.
#
# Las Areas se leen del modelo activo y/o de los vinculos cargados, cada una
# ya transformada a coordenadas del modelo activo, y se agrupan por NOMBRE de
# nivel: un LevelId del vinculo no significa nada en el anfitrion, pero
# "Nivel 5" si.

class MapaEspacial(object):

    def __init__(self, doc, tol_nivel_m=None, usar_host=True,
                 usar_vinculos=True, filtro_vinculo="",
                 param_nivel=PARAM_NIVEL_DEF):
        self.doc = doc
        if tol_nivel_m is None:
            tol_nivel_m = TOL_NIVEL_INFERIOR_M
        self.tol_nivel_inf_ft = tol_nivel_m / FT
        self.tol_elev_ft = TOL_ELEV_VINCULO_M / FT
        self.param_nivel = (param_nivel or "").strip()

        # Niveles del modelo activo. Solo los usa el respaldo por Z.
        self.niveles = sorted(
            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(),
            key=lambda n: n.ProjectElevation
        )

        # El rango fisico de Z de un nivel depende de la separacion entre
        # niveles, NO de si ese nivel tiene Areas dibujadas. Se calcula para
        # TODOS, tengan o no Areas.
        self.rango_por_nivel = dict(
            (n.Id, self._rango_z(n)) for n in self.niveles
        )

        # {clave de nivel -> [(nombre, polis, bb, area), ...]} ordenado de
        # menor a mayor area, para que el Area mas chica (balcon) gane sobre
        # la que la contiene.
        self.mapa_por_nivel = {}
        self.elev_por_clave = {}    # clave -> elevacion en coords del activo
        self.nombre_por_clave = {}  # clave -> nombre legible del nivel
        self.n_areas_depto = 0
        self.fuentes = []           # [(origen, n_areas, n_areas_depto)]
        self.vinculos_descargados = []

        self._clave_host = {}       # LevelId del activo -> clave
        self._clave_texto = {}      # texto de "Area Cuantif" -> clave o None
        self._rector = {}           # ElementId -> (clave o None, origen)
        self.rector_param = 0       # elementos con nivel rector del parametro
        self.rector_z = 0           # sin parametro: nivel por Z
        self.rector_noreco = 0      # parametro con un valor que no es nivel
        self.noreco_valores = {}    # valor no reconocido -> cuantos

        if usar_host:
            self._cargar_areas(doc, None, "Modelo activo")
        if usar_vinculos:
            self._cargar_vinculos(filtro_vinculo)

        for k in self.mapa_por_nivel:
            self.mapa_por_nivel[k].sort(key=lambda x: x[3])

    def _rango_z(self, nivel_actual):
        elev = nivel_actual.ProjectElevation
        idx = None
        for i, n in enumerate(self.niveles):
            if n.Id == nivel_actual.Id:
                idx = i
                break
        if idx is not None and idx > 0:
            elev_abajo = self.niveles[idx - 1].ProjectElevation
            z_min = elev - (elev - elev_abajo) * 0.9
        else:
            z_min = elev - (1.5 / FT)
        if idx is not None and idx < len(self.niveles) - 1:
            elev_arriba = self.niveles[idx + 1].ProjectElevation
            z_max = elev + (elev_arriba - elev) * 0.95
        else:
            z_max = elev + (4.0 / FT)
        return z_min, z_max

    # -- carga de Areas -------------------------------------------------------

    def _cargar_vinculos(self, filtro):
        """Lee las Areas que tiene PROPIAMENTE cada vinculo de Revit cargado."""
        clave_filtro = normalizar(filtro)
        try:
            instancias = DB.FilteredElementCollector(self.doc) \
                .OfClass(DB.RevitLinkInstance) \
                .ToElements()
        except Exception:
            instancias = []

        for inst in instancias:
            nombre = nombre_elemento(inst) or "Vinculo {}".format(eid_int(inst.Id))
            if clave_filtro and clave_filtro not in normalizar(nombre):
                continue
            try:
                ldoc = inst.GetLinkDocument()
            except Exception:
                ldoc = None
            if ldoc is None:
                # Vinculo descargado o sin encontrar: no hay Areas que leer.
                self.vinculos_descargados.append(nombre)
                continue
            try:
                trf = inst.GetTotalTransform()
            except Exception:
                trf = None
            self._cargar_areas(ldoc, trf, nombre)

    def _cargar_areas(self, doc_origen, trf, origen):
        """Carga las Areas de un documento ya TESELADAS y en coordenadas del
        modelo activo.

        CAMBIO CLAVE v1.1: antes la teselacion se hacia dentro del test de
        punto-en-poligono, o sea una vez por cada elemento contra cada Area.
        Medido en el modelo real: 7800 elementos x 3 puntos x ~40 Areas por
        nivel x ~10 curvas cada una son millones de llamadas a Tessellate().
        Ahora cada Area se tesela UNA vez al arrancar y se guarda como lista
        de puntos (x, y) mas su caja envolvente, que descarta de un golpe casi
        todas las comparaciones.

        v2.0: 'trf' es la transformacion del vinculo (None en el modelo
        activo). Se aplica a cada punto y a la elevacion del nivel.
        """
        opt = DB.SpatialElementBoundaryOptions()
        try:
            areas = DB.FilteredElementCollector(doc_origen) \
                .OfCategory(DB.BuiltInCategory.OST_Areas) \
                .WhereElementIsNotElementType() \
                .ToElements()
        except Exception:
            areas = []

        n_areas = 0
        n_depto = 0
        for esp in areas:
            # Area == 0 descarta las Areas no colocadas. En este modelo hay un
            # esquema "Areas Prefeitura" entero sin colocar (A.NAd, A.Ad,
            # A.Isenta) que si no se filtra contamina la votacion.
            try:
                if esp.Area == 0:
                    continue
            except Exception:
                continue

            p = esp.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
            if not p:
                continue
            nombre = p.AsString()
            if not nombre:
                continue

            nivel = doc_origen.GetElement(esp.LevelId)
            if not nivel:
                continue
            nombre_nivel = nombre_elemento(nivel)
            clave = clave_nivel(nombre_nivel)
            if not clave:
                continue

            try:
                loops = esp.GetBoundarySegments(opt)
            except Exception:
                loops = None
            if not loops:
                continue

            polis = []
            x0 = y0 = 1e12
            x1 = y1 = -1e12
            for loop in loops:
                pts = []
                for seg in loop:
                    try:
                        tess = seg.GetCurve().Tessellate()
                    except Exception:
                        continue
                    for q in tess:
                        if trf is not None:
                            q = trf.OfPoint(q)
                        pts.append((q.X, q.Y))
                        if q.X < x0: x0 = q.X
                        if q.X > x1: x1 = q.X
                        if q.Y < y0: y0 = q.Y
                        if q.Y > y1: y1 = q.Y
                if len(pts) > 2:
                    polis.append(pts)
            if not polis:
                continue

            elev = nivel.ProjectElevation
            if trf is not None:
                elev = trf.OfPoint(DB.XYZ(0, 0, elev)).Z
            self.elev_por_clave.setdefault(clave, elev)
            self.nombre_por_clave.setdefault(clave, nombre_nivel)

            self.mapa_por_nivel.setdefault(clave, []).append(
                (nombre, polis, (x0, y0, x1, y1), esp.Area))
            n_areas += 1
            if area_a_depto(nombre):
                n_depto += 1

        self.n_areas_depto += n_depto
        self.fuentes.append((origen, n_areas, n_depto))

    # -- geometria 2D ---------------------------------------------------------

    @staticmethod
    def punto_en_poligono(x, y, polis, bb):
        """Cruce de rayo sobre poligonos ya teselados, con rechazo por caja."""
        if x < bb[0] or x > bb[2] or y < bb[1] or y > bb[3]:
            return False
        dentro = False
        for pts in polis:
            for i in range(len(pts) - 1):
                ax, ay = pts[i]
                bx, by = pts[i + 1]
                if (ay > y) != (by > y):
                    den = by - ay
                    if abs(den) < 1e-12:
                        continue
                    if x < (bx - ax) * (y - ay) / den + ax:
                        dentro = not dentro
        return dentro

    # -- niveles: respaldo por Z ----------------------------------------------

    def nivel_por_z(self, z):
        """El nivel mas alto que queda POR DEBAJO de z: sobre el que se apoya."""
        mejor = None
        for n in self.niveles:
            if n.ProjectElevation <= z + self.tol_nivel_inf_ft:
                if mejor is None or n.ProjectElevation > mejor.ProjectElevation:
                    mejor = n
        if mejor is None and self.niveles:
            mejor = self.niveles[0]
        return mejor

    def niveles_candidatos(self, z):
        """Niveles cuyas Areas se prueban para ese punto, en orden.

        Primero el propio. Despues solo niveles INFERIORES cuyo rango tambien
        contenga z, para no dejar zonas muertas junto a la losa. Nunca un nivel
        por encima: la tuberia de un piso no puede pertenecer al depto de
        arriba, y permitirlo era justo lo que producia "Balcon 317" en tuberia
        de Nivel 2 en el script de hidraulica.
        """
        base = self.nivel_por_z(z)
        if base is None:
            return []
        cand = []
        for n in self.niveles:
            if n.Id not in self.rango_por_nivel:
                continue
            if n.ProjectElevation > base.ProjectElevation:
                continue
            z_min, z_max = self.rango_por_nivel[n.Id]
            if n.Id != base.Id and not (z_min <= z <= z_max):
                continue
            prioridad = 0 if n.Id == base.Id else 1
            cand.append((prioridad, -n.ProjectElevation, n))
        cand.sort(key=lambda t: (t[0], t[1]))
        return [n for _, _, n in cand]

    # -- niveles: clave comun entre activo, vinculos y "Area Cuantif" ---------

    def _clave_por_elevacion(self, z):
        """Clave del nivel con Areas cuya elevacion coincide con z."""
        mejor = None
        mejor_d = None
        for k in self.mapa_por_nivel:
            elev = self.elev_por_clave.get(k)
            if elev is None:
                continue
            d = abs(elev - z)
            if d <= self.tol_elev_ft and (mejor_d is None or d < mejor_d):
                mejor, mejor_d = k, d
        return mejor

    def clave_de_nivel_host(self, nivel):
        """Clave de Areas para un nivel del modelo activo.

        Primero por nombre. Si el vinculo llama distinto a ese piso, por
        elevacion. Si tampoco, la clave queda con su propio nombre y
        simplemente no tiene Areas.
        """
        k = self._clave_host.get(nivel.Id)
        if k is not None:
            return k
        k = clave_nivel(nombre_elemento(nivel))
        if k not in self.mapa_por_nivel:
            k = self._clave_por_elevacion(nivel.ProjectElevation) or k
        self.elev_por_clave.setdefault(k, nivel.ProjectElevation)
        self.nombre_por_clave.setdefault(k, nombre_elemento(nivel))
        self._clave_host[nivel.Id] = k
        return k

    def clave_de_texto(self, texto):
        """Clave de nivel para el valor de "Area Cuantif", o None si ese valor
        no corresponde a ningun nivel (ni con Areas ni del modelo activo)."""
        if texto in self._clave_texto:
            return self._clave_texto[texto]
        k = clave_nivel(texto)
        res = None
        if k in self.mapa_por_nivel:
            res = k
        else:
            for n in self.niveles:
                if clave_nivel(nombre_elemento(n)) == k:
                    res = self.clave_de_nivel_host(n)
                    break
        self._clave_texto[texto] = res
        return res

    def rector(self, elemento):
        """Nivel rector del elemento: (clave, origen).

        origen = "param"          -> lo dice "Area Cuantif" y es un nivel.
                 "z"              -> el parametro esta vacio: se usa la Z.
                 "no_reconocido"  -> el parametro trae algo que no es un nivel
                                     ("Azotea", "Dpto. 513"...): se usa la Z.
        clave es None cuando hay que caer a la Z.
        """
        if elemento is None:
            return None, "z"
        r = self._rector.get(elemento.Id)
        if r is not None:
            return r
        texto = leer_texto_parametro(elemento, self.param_nivel) \
            if self.param_nivel else ""
        if not texto:
            r = (None, "z")
            self.rector_z += 1
        else:
            k = self.clave_de_texto(texto)
            if k:
                r = (k, "param")
                self.rector_param += 1
            else:
                r = (None, "no_reconocido")
                self.rector_noreco += 1
                self.noreco_valores[texto] = self.noreco_valores.get(texto, 0) + 1
        self._rector[elemento.Id] = r
        return r

    def nivel_de(self, elemento, punto=None):
        """(nombre legible, elevacion) del nivel que manda en el elemento."""
        clave, _ = self.rector(elemento)
        if clave:
            return (self.nombre_por_clave.get(clave, clave),
                    self.elev_por_clave.get(clave))
        if punto is None:
            return None, None
        niv = self.nivel_por_z(punto.Z)
        if niv is None:
            return None, None
        return nombre_elemento(niv), niv.ProjectElevation

    # -- consulta -------------------------------------------------------------

    def nombre_area_en(self, punto, clave=None):
        """Nombre del Area que contiene el punto, o None.

        Con 'clave' (nivel rector de "Area Cuantif") solo se prueban las Areas
        de ESE nivel. Sin ella, los candidatos por Z.
        """
        if not punto:
            return None
        if clave is not None:
            claves = [clave]
        else:
            claves = [self.clave_de_nivel_host(n)
                      for n in self.niveles_candidatos(punto.Z)]
        for k in claves:
            for nombre, polis, bb, _ in self.mapa_por_nivel.get(k, ()):
                if self.punto_en_poligono(punto.X, punto.Y, polis, bb):
                    return nombre
        return None

    def depto_en(self, punto, clave=None):
        """Departamento al que pertenece el punto, ya normalizado, o None."""
        return area_a_depto(self.nombre_area_en(punto, clave))


# =============================================================================
#  2. PUNTOS REPRESENTATIVOS
# =============================================================================

def centroide(elemento):
    """Punto que representa al elemento.

    Para accesorios se prefiere el promedio de los conectores y NO
    Location.Point: varias familias de accesorio no reflejan el offset en su
    punto de insercion -se queda clavado en la elevacion del nivel anfitrion-,
    asi que todas las valvulas hospedadas en el mismo nivel devuelven el mismo
    Z. Los conectores en cambio siempre tienen la posicion fisica real: es lo
    que Revit usa para unir tuberias, no puede mentir.
    """
    loc = elemento.Location
    if isinstance(loc, DB.LocationCurve):
        try:
            return loc.Curve.Evaluate(0.5, True)
        except Exception:
            pass
    try:
        if isinstance(elemento, DB.FamilyInstance):
            conns = get_conectores(elemento)
            if conns:
                n = float(len(conns))
                return DB.XYZ(sum(c.Origin.X for c in conns) / n,
                              sum(c.Origin.Y for c in conns) / n,
                              sum(c.Origin.Z for c in conns) / n)
    except Exception:
        pass
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    try:
        bb = elemento.get_BoundingBox(None)
        if bb:
            return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0,
                          (bb.Min.Y + bb.Max.Y) / 2.0,
                          (bb.Min.Z + bb.Max.Z) / 2.0)
    except Exception:
        pass
    return None


def puntos_muestra(elemento):
    """Varios puntos del elemento, para votar mejor.

    Un tramo largo que entra al depto puede tener su punto medio en el pasillo.
    Muestreando extremos y medio, el voto refleja por donde pasa de verdad.
    """
    pts = []
    loc = elemento.Location
    if isinstance(loc, DB.LocationCurve):
        try:
            c = loc.Curve
            for t in (0.15, 0.5, 0.85):
                pts.append(c.Evaluate(t, True))
        except Exception:
            pass
    if not pts:
        p = centroide(elemento)
        if p:
            pts.append(p)
    return pts


def longitud_ft(elemento):
    try:
        p = elemento.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
        if p:
            return p.AsDouble()
    except Exception:
        pass
    loc = elemento.Location
    if isinstance(loc, DB.LocationCurve):
        try:
            return loc.Curve.Length
        except Exception:
            pass
    return 0.0


def diametro_texto(elemento):
    for bip in (DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM,
                DB.BuiltInParameter.RBS_CALCULATED_SIZE):
        try:
            p = elemento.get_Parameter(bip)
            if p:
                s = p.AsValueString() or p.AsString()
                if s:
                    return s.strip()
        except Exception:
            continue
    return "-"


def es_vertical(elemento):
    loc = elemento.Location
    if not isinstance(loc, DB.LocationCurve):
        return False
    try:
        p0 = loc.Curve.GetEndPoint(0)
        p1 = loc.Curve.GetEndPoint(1)
    except Exception:
        return False
    v = p1 - p0
    L = v.GetLength()
    if L < 1e-6:
        return False
    return (abs(v.Z) / L) >= UMBRAL_COSENO


# =============================================================================
#  3. MOTOR DE ASIGNACION
# =============================================================================

class Opciones(object):
    def __init__(self):
        self.parametro      = "Area/dpto"
        # 0.010 m no es un numero al azar: es el que salio del barrido sobre
        # este modelo. Con 0 quedan 4373 pedazos sueltos; con 0.010 bajan a
        # 1981 y los 110 departamentos siguen separados. Con 0.020 ya empiezan
        # a fusionarse ramales de deptos distintos (110 -> 107), y con 0.050
        # se pierden nueve. Subirlo de 0.010 es cambiar huecos por mezcla.
        self.tolerancia_m   = 0.010
        # Tolerancia de NIVEL. Ver el comentario de TOL_NIVEL_INFERIOR_M:
        # 0.005 para instalaciones que corren por el techo (gas), 0.15 para
        # las que corren por el piso (hidraulica, sanitaria).
        self.tol_nivel_m    = TOL_NIVEL_INFERIOR_M
        # Nivel rector: lo que diga este parametro manda sobre la Z.
        self.param_nivel    = PARAM_NIVEL_DEF
        # De donde salen las Areas: del modelo activo, de los vinculos, o de
        # ambos. El filtro deja solo los vinculos cuyo nombre lo contenga.
        self.areas_host     = True
        self.areas_vinculos = True
        self.filtro_vinculo = ""
        self.claves_medidor = CLAVES_MEDIDOR_DEF
        self.claves_veto    = CLAVES_VETO_DEF
        self.claves_tanque  = CLAVES_TANQUE_DEF
        self.solo_vista     = True
        self.filtro_workset = "gas"     # vacio = no filtrar por subproyecto
        self.marcar_dups    = True
        self.escribir       = False


class Resultado(object):
    def __init__(self):
        self.valores          = {}   # ElementId -> texto
        self.elementos        = {}   # ElementId -> Element
        self.medidores        = []
        self.medidor_depto    = {}   # ElementId(medidor) -> depto o None
        self.bancos           = []   # [[ElementId de medidor, ...], ...]
        self.ramales_ok       = 0
        self.ramales_pend     = []   # [(medidor_id, n_elems, nivel_alcanzado)]
        self.columna_de_banco = {}   # medidor_id -> columna dominante de su banco
        self.ml_vertical      = 0.0  # ml de bajada que SI quedaron con depto
        self.cruces           = []   # componentes que tocan >1 medidor y tienen votos
        self.ambiguos         = []   # (depto, votos_1, depto2, votos_2, n)
        self.dups             = []   # ids marcados como duplicado
        self.n_comun          = 0
        self.n_escritos       = 0
        self.sin_param        = []   # elementos donde el parametro no existe / es de solo lectura
        self.conteo_cat       = {}
        self.aristas_conector = 0
        self.aristas_toler    = 0
        self.alertas_serie    = []
        self.med_familias     = {}   # "familia / tipo" -> cuantos
        self.tan_familias     = {}
        self.med_conns        = 0    # conectores MEP que tienen los medidores
        self.med_conns_ok     = 0    # de esos, cuantos reportan conexion
        self.med_sin_conn     = 0    # medidores SIN ningun conector
        self.n_sueltos        = 0    # puntas de conector sin conectar
        self.niv_ok           = 0    # nivel por Z == "Area Cuantif"
        self.niv_mal          = 0    # nivel por Z != "Area Cuantif" (manda el parametro)
        self.niv_sin          = 0    # sin "Area Cuantif" que comparar
        self.niv_ejemplos     = []
        self.rector_param     = 0    # elementos con nivel rector de "Area Cuantif"
        self.rector_z         = 0    # sin parametro: nivel por Z
        self.rector_noreco    = 0    # parametro con valor que no es un nivel
        self.noreco_valores   = {}   # valor no reconocido -> cuantos
        self.fuentes_areas    = []   # [(origen, n_areas, n_areas_depto)]
        self.vinculos_descargados = []
        self.error            = None


def recolectar(doc, vista, opt):
    """Todos los elementos de red dentro del alcance elegido."""
    cats = List[DB.BuiltInCategory](CATS_RED)
    filtro = DB.ElementMulticategoryFilter(cats)

    if opt.solo_vista and vista is not None:
        col = DB.FilteredElementCollector(doc, vista.Id)
    else:
        col = DB.FilteredElementCollector(doc)

    elems = col.WherePasses(filtro).WhereElementIsNotElementType().ToElements()

    clave_ws = normalizar(opt.filtro_workset)
    if not clave_ws:
        return list(elems)

    # Filtro por subproyecto. En este modelo todo el gas vive en
    # "Instalacion Gas", asi que basta la palabra "gas".
    fuera = []
    try:
        tabla = doc.GetWorksetTable()
    except Exception:
        tabla = None
    if tabla is None:
        return list(elems)

    for e in elems:
        try:
            ws = tabla.GetWorkset(e.WorksetId)
            if ws and clave_ws in normalizar(ws.Name):
                fuera.append(e)
        except Exception:
            continue
    return fuera


def construir_grafo(elementos, tol_ft, res):
    """Adyacencia por conectores, con union opcional por cercania de puntas."""
    ids = set(e.Id for e in elementos)
    grafo = dict((e.Id, set()) for e in elementos)
    sueltos = []

    for e in elementos:
        id_a = e.Id
        for c in get_conectores(e):
            try:
                conectado = c.IsConnected
            except Exception:
                conectado = False
            if conectado:
                try:
                    refs = list(c.AllRefs)
                except Exception:
                    refs = []
                for ref in refs:
                    try:
                        if ref.ConnectorType == DB.ConnectorType.Logical:
                            continue
                        id_b = ref.Owner.Id
                    except Exception:
                        continue
                    if id_b == id_a or id_b not in ids:
                        continue
                    grafo[id_a].add(id_b)
                    grafo[id_b].add(id_a)
                    res.aristas_conector += 1
            else:
                res.n_sueltos += 1
                try:
                    sueltos.append((id_a, c.Origin))
                except Exception:
                    pass

    # Union por tolerancia: rescata puntas que quedaron a milimetros sin
    # conectar. Con tolerancia 0 no se hace nada (maxima precision).
    if tol_ft > 0 and sueltos:
        # Rejilla espacial para no comparar todos contra todos (son miles).
        celda = max(tol_ft, 1e-6)
        rejilla = {}
        for idx, (id_a, pt) in enumerate(sueltos):
            k = (int(math.floor(pt.X / celda)),
                 int(math.floor(pt.Y / celda)),
                 int(math.floor(pt.Z / celda)))
            rejilla.setdefault(k, []).append(idx)

        vistos = set()
        for idx, (id_a, pt_a) in enumerate(sueltos):
            kx = int(math.floor(pt_a.X / celda))
            ky = int(math.floor(pt_a.Y / celda))
            kz = int(math.floor(pt_a.Z / celda))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for jdx in rejilla.get((kx + dx, ky + dy, kz + dz), ()):
                            if jdx <= idx:
                                continue
                            par = (idx, jdx)
                            if par in vistos:
                                continue
                            vistos.add(par)
                            id_b, pt_b = sueltos[jdx]
                            if id_a == id_b:
                                continue
                            if pt_a.DistanceTo(pt_b) <= tol_ft:
                                grafo[id_a].add(id_b)
                                grafo[id_b].add(id_a)
                                res.aristas_toler += 1
    return grafo


def componentes_cortando(grafo, cortados):
    """Componentes conexas del grafo ignorando los nodos 'cortados'.

    Los medidores siguen existiendo como elementos, pero no propagan
    conectividad: al quitarlos, la red se parte sola en red general por un lado
    y un ramal por departamento por el otro. Eso es todo el truco.
    """
    visitados = set(cortados)
    comps = []
    for nodo in grafo.keys():
        if nodo in visitados:
            continue
        cola = deque([nodo])
        comp = set()
        while cola:
            actual = cola.popleft()
            if actual in visitados:
                continue
            visitados.add(actual)
            comp.add(actual)
            for vec in grafo.get(actual, ()):
                if vec not in visitados:
                    cola.append(vec)
        if comp:
            comps.append(comp)
    return comps


def votar_depto(comp, elementos_por_id, mapa):
    """Departamento mayoritario entre los elementos de la componente.

    Se vota por punto de muestra: un tramo cuenta una vez por cada punto suyo
    que caiga dentro de un Area de departamento. Asi un tramo que entra al
    depto pesa mas que uno que solo lo roza.

    Cada elemento solo mira las Areas de su nivel rector ("Area Cuantif"); si
    no lo tiene, las de su nivel por Z.
    """
    conteo = {}
    for id_n in comp:
        e = elementos_por_id.get(id_n)
        if e is None:
            continue
        clave, _ = mapa.rector(e)
        for p in puntos_muestra(e):
            d = mapa.depto_en(p, clave)
            if d:
                conteo[d] = conteo.get(d, 0) + 1
    if not conteo:
        return None, conteo
    ganador = max(conteo, key=conteo.get)
    return ganador, conteo


def agrupar_bancos(medidores, puntos):
    """Agrupa medidores por cercania: cada grupo es un banco.

    Sirve para la validacion de serie (un banco alimenta una columna de deptos)
    y para poder decirte, cuando falte uno, que piso es el que falta.
    """
    r_ft = RADIO_BANCO_M / FT
    pend = [m.Id for m in medidores if puntos.get(m.Id)]
    bancos = []
    while pend:
        semilla = pend.pop(0)
        grupo = [semilla]
        cambio = True
        while cambio:
            cambio = False
            for otro in list(pend):
                p_o = puntos[otro]
                for g in grupo:
                    p_g = puntos[g]
                    if p_o.DistanceTo(p_g) <= r_ft:
                        grupo.append(otro)
                        pend.remove(otro)
                        cambio = True
                        break
        bancos.append(grupo)
    bancos.sort(key=lambda g: (-len(g), eid_int(g[0])))
    return bancos


def detectar_duplicados(elementos):
    """Ejemplares identicos en la misma ubicacion: se cuentan doble en tablas.

    En este modelo Revit ya avisa de 172 elementos de gas duplicados, entre
    ellos dos bancos de medidores completos.
    """
    def clave_geo(e):
        loc = e.Location
        try:
            if isinstance(loc, DB.LocationCurve):
                c = loc.Curve
                a = c.GetEndPoint(0)
                b = c.GetEndPoint(1)
                pa = (round(a.X, 4), round(a.Y, 4), round(a.Z, 4))
                pb = (round(b.X, 4), round(b.Y, 4), round(b.Z, 4))
                return tuple(sorted([pa, pb]))
            if isinstance(loc, DB.LocationPoint):
                p = loc.Point
                return (round(p.X, 4), round(p.Y, 4), round(p.Z, 4))
        except Exception:
            pass
        return None

    grupos = {}
    for e in elementos:
        g = clave_geo(e)
        if g is None:
            continue
        try:
            cat = eid_int(e.Category.Id) if e.Category else 0
            tip = eid_int(e.GetTypeId())
        except Exception:
            cat, tip = 0, 0
        grupos.setdefault((cat, tip, g), []).append(e.Id)

    dups = []
    for k, ids in grupos.items():
        if len(ids) > 1:
            ids_ord = sorted(ids, key=eid_int)
            dups.extend(ids_ord[1:])   # el primero se conserva
    return dups


# =============================================================================
#  4. PROCESO COMPLETO
# =============================================================================

def procesar(doc, vista, opt):
    res = Resultado()

    elementos = recolectar(doc, vista, opt)
    if not elementos:
        res.error = ("No se encontro ningun elemento de red en el alcance "
                     "elegido. Revisa la vista activa y el filtro de "
                     "subproyecto.")
        return res

    res.elementos = dict((e.Id, e) for e in elementos)

    for e in elementos:
        if e.Category:
            n = e.Category.Name
            res.conteo_cat[n] = res.conteo_cat.get(n, 0) + 1

    mapa = MapaEspacial(doc,
                        tol_nivel_m=getattr(opt, 'tol_nivel_m', TOL_NIVEL_INFERIOR_M),
                        usar_host=getattr(opt, 'areas_host', True),
                        usar_vinculos=getattr(opt, 'areas_vinculos', True),
                        filtro_vinculo=getattr(opt, 'filtro_vinculo', ""),
                        param_nivel=getattr(opt, 'param_nivel', PARAM_NIVEL_DEF))
    res.fuentes_areas = mapa.fuentes
    res.vinculos_descargados = mapa.vinculos_descargados
    if not (getattr(opt, 'areas_host', True) or getattr(opt, 'areas_vinculos', True)):
        res.error = ("No se eligio ninguna fuente de Areas. Activa 'Areas del "
                     "modelo activo', 'Areas de los vinculos' o ambas.")
        return res
    if not mapa.mapa_por_nivel:
        res.error = ("No hay Areas utilizables (con nombre, area > 0 y "
                     "limites) en las fuentes elegidas. Sin Areas no hay a "
                     "que asignar.")
        if mapa.vinculos_descargados:
            res.error += (" Hay vinculos DESCARGADOS que podrian tenerlas: "
                          "{}. Cargalos en Administrar vinculos."
                          .format(", ".join(mapa.vinculos_descargados)))
        return res
    if mapa.n_areas_depto == 0:
        res.error = ("Hay Areas pero ninguna se llama como departamento. "
                     "Este script espera nombres tipo 'Dpto. 613', "
                     "'Balcon 613' o 'Terraza 112'.")
        return res

    # --- medidores y tanque ---------------------------------------------------
    claves_med = lista_claves(opt.claves_medidor)
    claves_tan = lista_claves(opt.claves_tanque)
    claves_veto = lista_claves(getattr(opt, 'claves_veto', CLAVES_VETO_DEF))

    medidores = [e for e in elementos
                 if coincide_claves(e, claves_med)
                 and not coincide_claves(e, claves_veto)]
    tanques   = set(e.Id for e in elementos if coincide_claves(e, claves_tan))
    res.medidores = medidores

    # Diagnostico de que se reconocio como medidor y como tanque. Si aqui
    # aparece una familia que no toca, el corte se esta haciendo mal.
    for e in medidores:
        k = "{} / {}".format(nombre_familia(e), nombre_tipo(e))
        res.med_familias[k] = res.med_familias.get(k, 0) + 1
    for i in tanques:
        e = res.elementos.get(i)
        if e is not None:
            k = "{} / {}".format(nombre_familia(e), nombre_tipo(e))
            res.tan_familias[k] = res.tan_familias.get(k, 0) + 1

    # Salud de los conectores de los medidores. Si un medidor no tiene
    # conectores MEP, no hay nada que cortar: la familia se modelo como un
    # bloque decorativo y el corte en el medidor queda desactivado para el.
    for m in medidores:
        cs = get_conectores(m)
        if not cs:
            res.med_sin_conn += 1
        for c in cs:
            res.med_conns += 1
            try:
                if c.IsConnected:
                    res.med_conns_ok += 1
            except Exception:
                pass

    if not medidores:
        res.error = ("No se encontro ningun medidor. Ajusta las palabras clave "
                     "(hoy: '{}'). En este modelo la familia se llama "
                     "'Generic-Gas_Meter' y el tipo 'Medidor Kumho KG-2 de 10 "
                     "m3/h', asi que 'medidor' o 'gas_meter' los "
                     "atrapa.".format(opt.claves_medidor))
        return res

    ids_medidor = set(m.Id for m in medidores)

    # --- grafo y corte en el medidor -----------------------------------------
    tol_ft = (opt.tolerancia_m or 0.0) / FT
    grafo = construir_grafo(elementos, tol_ft, res)
    comps = componentes_cortando(grafo, ids_medidor)

    # Para cada componente, que medidores la tocan.
    medidores_de_comp = []
    comp_de_nodo = {}
    for i, comp in enumerate(comps):
        for n in comp:
            comp_de_nodo[n] = i
        tocan = set()
        for n in comp:
            for vec in grafo.get(n, ()):
                if vec in ids_medidor:
                    tocan.add(vec)
        medidores_de_comp.append(tocan)

    # Umbral de azotea: por debajo del medidor mas bajo, con holgura. Se usa
    # solo como red de seguridad para clasificar como comun lo que quedo
    # colgado arriba sin voto.
    puntos = {}
    for e in elementos:
        puntos[e.Id] = centroide(e)
    z_medidores = [puntos[m.Id].Z for m in medidores if puntos.get(m.Id)]
    z_azotea = (min(z_medidores) - (1.5 / FT)) if z_medidores else None

    # --- clasificar cada componente -------------------------------------------
    depto_de_comp = {}

    for i, comp in enumerate(comps):
        tocan = medidores_de_comp[i]
        tiene_tanque = bool(tanques & comp)
        ganador, conteo = votar_depto(comp, res.elementos, mapa)

        if tiene_tanque:
            # La acometida es comun aunque por accidente caiga dentro de un Area.
            depto_de_comp[i] = VALOR_COMUN
            res.n_comun += len(comp)
            continue

        if len(tocan) >= 2:
            if ganador:
                # Dos medidores alimentando la misma red es un cruce entre
                # ramales: error de modelado, no red general. Se asigna por
                # mayoria pero se reporta para que lo revises.
                res.cruces.append((i, len(tocan), len(comp), ganador))
                depto_de_comp[i] = ganador
            else:
                depto_de_comp[i] = VALOR_COMUN
                res.n_comun += len(comp)
            continue

        if len(tocan) == 1:
            if ganador:
                depto_de_comp[i] = ganador
                res.ramales_ok += 1
                # Votacion reñida: se avisa.
                otros = sorted((v for k, v in conteo.items() if k != ganador),
                               reverse=True)
                if otros and otros[0] >= 0.30 * conteo[ganador]:
                    segundo = [k for k, v in conteo.items()
                               if k != ganador and v == otros[0]]
                    res.ambiguos.append((ganador, conteo[ganador],
                                         segundo[0] if segundo else "?",
                                         otros[0], len(comp)))
            else:
                # CAMBIO CLAVE v1.2, medido en el modelo real: que un trozo
                # cuelgue de un medidor y no alcance ningun Area de
                # departamento NO es razon para castigarlo con "Sin asignar".
                # Pasa cuando la bajada esta rota mas abajo: el trozo se queda
                # en azotea, junto al banco. Con la regla anterior esos trozos
                # se llevaban a "Sin asignar" a 1348 elementos que antes se
                # resolvian bien. Ahora el medidor se REPORTA como pendiente,
                # pero el trozo se resuelve con el mismo criterio que cualquier
                # huerfano: posicion, y si no, red general.
                zs = [puntos[n].Z for n in comp if puntos.get(n)]
                res.ramales_pend.append(
                    (list(tocan)[0], len(comp),
                     nivel_mas_bajo(comp, res.elementos, puntos, mapa)))
                zs_ord = sorted(zs)
                if z_azotea is not None and zs_ord and zs_ord[len(zs_ord) // 2] >= z_azotea:
                    depto_de_comp[i] = VALOR_COMUN
                    res.n_comun += len(comp)
                else:
                    depto_de_comp[i] = VALOR_SIN
            continue

        # Componente huerfana: no toca ningun medidor.
        if ganador:
            # Cae dentro de un depto: se acepta por posicion, es lo unico que
            # hay. Suele ser tuberia interior que quedo desconectada.
            depto_de_comp[i] = ganador
        elif z_azotea is not None and puntos_comp_arriba(comp, puntos, z_azotea):
            depto_de_comp[i] = VALOR_COMUN
            res.n_comun += len(comp)
        else:
            depto_de_comp[i] = VALOR_SIN

    # --- volcar a valores ------------------------------------------------------
    for i, comp in enumerate(comps):
        v = depto_de_comp.get(i, VALOR_SIN)
        for n in comp:
            res.valores[n] = v

    # --- el medidor toma el valor de SU ramal ----------------------------------
    for m in medidores:
        cand = {}
        for vec in grafo.get(m.Id, ()):
            v = res.valores.get(vec)
            if v and v not in (VALOR_COMUN, VALOR_SIN):
                cand[v] = cand.get(v, 0) + 1
        if cand:
            valor = max(cand, key=cand.get)
        else:
            # Sin ramal resuelto: o su bajada no esta modelada, o no esta
            # conectada, o no llega a ningun Area de departamento.
            valor = VALOR_SIN
        res.valores[m.Id] = valor
        res.medidor_depto[m.Id] = None if valor in (VALOR_COMUN, VALOR_SIN) else valor

    # Metros de tramo vertical que quedaron con departamento. Es exactamente
    # lo que el criterio de "Ducto vertical" tiraba a la basura.
    for id_n, v in res.valores.items():
        if v in (VALOR_COMUN, VALOR_SIN, VALOR_DUPLICADO):
            continue
        e = res.elementos.get(id_n)
        if e is not None and es_tubo(e) and es_vertical(e):
            res.ml_vertical += longitud_ft(e) * FT

    # --- nivel rector: "Area Cuantif" contra lo que diria la Z ----------------
    # Osvaldo mantiene a mano el parametro "Area Cuantif" con el nivel de cada
    # elemento. Desde v2.0 ese parametro es el que MANDA; aqui solo se mide
    # cuantas veces corrige a la Z, para que se vea cuanto pesa el cambio.
    for e in elementos:
        mapa.rector(e)   # asegura el conteo aunque el elemento no haya votado
    res.rector_param = mapa.rector_param
    res.rector_z = mapa.rector_z
    res.rector_noreco = mapa.rector_noreco
    res.noreco_valores = mapa.noreco_valores

    for e in elementos:
        p = puntos.get(e.Id)
        if p is None:
            continue
        clave, origen = mapa.rector(e)
        if origen != "param":
            res.niv_sin += 1
            continue
        dicho = mapa.nombre_por_clave.get(clave, clave)
        niv = mapa.nivel_por_z(p.Z)
        calc = nombre_elemento(niv) if niv is not None else ""
        if niv is not None and mapa.clave_de_nivel_host(niv) == clave:
            res.niv_ok += 1
        else:
            res.niv_mal += 1
            if len(res.niv_ejemplos) < 10:
                res.niv_ejemplos.append((e.Id, dicho, calc, p.Z))

    # --- bancos y validacion de serie -----------------------------------------
    res.bancos = agrupar_bancos(medidores, puntos)
    validar_series(res)

    # --- duplicados ------------------------------------------------------------
    if opt.marcar_dups:
        res.dups = detectar_duplicados(elementos)
        for did in res.dups:
            res.valores[did] = VALOR_DUPLICADO

    # --- escritura -------------------------------------------------------------
    if opt.escribir:
        escribir_valores(doc, res, opt.parametro)

    return res


def nivel_mas_bajo(comp, elementos_por_id, puntos, mapa):
    """Nombre del nivel mas bajo al que llega la componente, segun el nivel
    rector de cada elemento (y la Z para los que no lo tienen)."""
    mejor = None
    for n in comp:
        nombre, elev = mapa.nivel_de(elementos_por_id.get(n), puntos.get(n))
        if nombre is None or elev is None:
            continue
        if mejor is None or elev < mejor[0]:
            mejor = (elev, nombre)
    return mejor[1] if mejor else "?"


def puntos_comp_arriba(comp, puntos, z_umbral):
    zs = [puntos[n].Z for n in comp if puntos.get(n)]
    if not zs:
        return False
    zs.sort()
    mediana = zs[len(zs) // 2]
    return mediana >= z_umbral


def validar_series(res):
    """Un banco alimenta una columna de deptos: mismos dos ultimos digitos,
    un piso cada uno. Cualquier cosa fuera de ese patron se reporta."""
    for banco in res.bancos:
        deptos = []
        faltan = []
        for mid in banco:
            d = res.medidor_depto.get(mid)
            if d:
                deptos.append((mid, d))
            else:
                faltan.append(mid)

        if not deptos:
            if faltan:
                res.alertas_serie.append(
                    ("sin_resolver", banco, [], faltan))
            continue

        columnas = {}
        for mid, d in deptos:
            piso, col = numero_de_depto(d)
            columnas.setdefault(col, []).append((mid, d, piso))

        # Departamento repetido dentro del mismo banco: casi siempre es un
        # banco duplicado encima de otro, o dos medidores para el mismo depto.
        vistos = {}
        for mid, d in deptos:
            vistos.setdefault(d, []).append(mid)
        repetidos = [(d, ids) for d, ids in vistos.items() if len(ids) > 1]
        if repetidos:
            res.alertas_serie.append(("depto_repetido", banco, repetidos, []))

        col_principal = max(columnas, key=lambda c: len(columnas[c]))
        for mid in banco:
            res.columna_de_banco[mid] = col_principal
        intrusos = []
        for c, lst in columnas.items():
            if c != col_principal:
                intrusos.extend(lst)

        pisos_ok = sorted(set(p for _, _, p in columnas[col_principal] if p))
        if intrusos:
            res.alertas_serie.append(
                ("columna_mezclada", banco, intrusos, []))
        if faltan:
            res.alertas_serie.append(
                ("falta_piso", banco, [(col_principal, pisos_ok)], faltan))


def escribir_valores(doc, res, nombre_parametro):
    t = DB.Transaction(doc, "Asignacion de gas por departamento")
    t.Start()
    try:
        for id_n, valor in res.valores.items():
            elem = res.elementos.get(id_n) or doc.GetElement(id_n)
            if elem is None:
                continue
            p = elem.LookupParameter(nombre_parametro)
            if p is None or p.IsReadOnly:
                res.sin_param.append(id_n)
                continue
            try:
                actual = p.AsString() or ""
            except Exception:
                actual = ""
            if actual != valor:
                p.Set(valor)
                res.n_escritos += 1
        t.Commit()
    except Exception as ex:
        t.RollBack()
        res.error = "Fallo al escribir: {}".format(ex)


# =============================================================================
#  5. REPORTE
# =============================================================================

def es_tubo(e):
    try:
        bic = e.Category.BuiltInCategory
    except Exception:
        return False
    return bic in (DB.BuiltInCategory.OST_PipeCurves,
                   DB.BuiltInCategory.OST_FlexPipeCurves)


def reportar_fuentes(res):
    """De donde salieron las Areas. Si un vinculo no aporta Areas de depto,
    aqui se ve de inmediato."""
    if res.fuentes_areas:
        filas = [[o, n, d] for o, n, d in res.fuentes_areas]
        output.print_table(filas,
                           columns=["Fuente", "Areas colocadas",
                                    "Areas de depto"],
                           title="Fuentes de Areas")
    if res.vinculos_descargados:
        output.print_md(
            "> **Vinculos descargados** (no se leyeron sus Areas): {}"
            .format(", ".join(res.vinculos_descargados)))


def reportar(res, opt, vista):
    output.close_others(all_open_outputs=True)
    output.set_title("Gas por departamento")

    modo = "ASIGNADO AL MODELO" if opt.escribir else "DIAGNOSTICO (no se toco el modelo)"
    output.print_md("# Instalacion de gas - asignacion por departamento")
    fuentes = []
    if getattr(opt, 'areas_host', True):
        fuentes.append("modelo activo")
    if getattr(opt, 'areas_vinculos', True):
        f = (getattr(opt, 'filtro_vinculo', "") or "").strip()
        fuentes.append("vinculos" + (" que contengan '{}'".format(f) if f else ""))
    output.print_md("**Modo:** {}  \n**Vista:** {}  \n"
                    "**Parametro:** `{}`  \n**Tolerancia:** {:.3f} m  \n"
                    "**Nivel rector:** `{}`  \n**Areas de:** {}"
                    .format(modo,
                            vista.Name if vista is not None else "(todo el modelo)",
                            opt.parametro, opt.tolerancia_m,
                            getattr(opt, 'param_nivel', "") or "(solo Z)",
                            ", ".join(fuentes) or "(ninguna)"))

    reportar_fuentes(res)

    if res.error:
        output.print_md("## No se pudo completar")
        output.print_md("> {}".format(res.error))
        return

    total = len(res.elementos)
    n_dep = sum(1 for v in res.valores.values()
                if v not in (VALOR_COMUN, VALOR_SIN, VALOR_DUPLICADO))
    n_sin = sum(1 for v in res.valores.values() if v == VALOR_SIN)
    n_com = sum(1 for v in res.valores.values() if v == VALOR_COMUN)

    # ---------------------------------------------------------------- resumen
    resumen = [
        ["Elementos de red en el alcance", total],
        ["Medidores detectados", len(res.medidores)],
        ["Bancos de medidores", len(res.bancos)],
        ["Ramales resueltos (1 medidor -> 1 depto)", res.ramales_ok],
        ["Elementos con departamento", n_dep],
        ["Elementos en '{}'".format(VALOR_COMUN), n_com],
        ["Elementos en '{}'".format(VALOR_SIN), n_sin],
        ["Elementos marcados '{}'".format(VALOR_DUPLICADO), len(res.dups)],
        ["ml de bajada vertical con depto (antes 'Ducto vertical')",
         round(res.ml_vertical, 2)],
        ["Parametros escritos", res.n_escritos if opt.escribir else 0],
    ]
    output.print_table(resumen, columns=["Concepto", "Cantidad"], title="Resumen")

    # ------------------------------------------------------------ salud
    # Este bloque es el termometro del modelo: dice si el corte en el medidor
    # pudo siquiera intentarse, y si la red esta conectada.
    output.print_md("## Salud de la red")
    output.print_md(
        "- Uniones leidas de conectores de Revit: **{}**\n"
        "- Uniones rescatadas por tolerancia: **{}**\n"
        "- Puntas de conector sueltas: **{}**"
        .format(res.aristas_conector, res.aristas_toler, res.n_sueltos))

    if res.medidores:
        output.print_md(
            "- Conectores en los medidores: **{}** (conectados: **{}**)\n"
            "- Medidores SIN ningun conector: **{} de {}**"
            .format(res.med_conns, res.med_conns_ok,
                    res.med_sin_conn, len(res.medidores)))

    if res.med_sin_conn and res.med_sin_conn >= 0.5 * len(res.medidores):
        output.print_md(
            "> **El corte en el medidor esta desactivado.** La familia del "
            "medidor no tiene conectores MEP, asi que no hay red que cortar "
            "en el: el medidor es un bloque suelto apoyado junto a la "
            "tuberia.\n>\n"
            "> El resultado de abajo sale del camino alterno -cada pedazo de "
            "red se resuelve por la posicion de sus propios elementos-, que "
            "funciona pero deja en '{}' toda la tuberia de azotea que en "
            "realidad ya es del departamento (la que va del medidor a su "
            "bajada).\n>\n"
            "> Se arregla de una vez y para siempre editando la familia del "
            "medidor: agregarle dos conectores de tuberia, uno de entrada y "
            "uno de salida, y recargarla. A partir de ahi cada ramal queda "
            "colgado de su medidor y la red general baja a lo que de verdad "
            "es comun.".format(VALOR_COMUN))

    # El nivel rector va primero: si esto sale mal, todo lo demas sobra.
    param_niv = getattr(opt, 'param_nivel', "") or ""
    tot_rect = res.rector_param + res.rector_z + res.rector_noreco
    if tot_rect:
        output.print_md(
            "**Nivel rector:** {} elementos toman el nivel de `{}`; {} no lo "
            "tienen lleno y {} traen un valor que no es ningun nivel (esos {} "
            "caen al calculo por Z, tolerancia {:.3f} m)."
            .format(res.rector_param, param_niv or "-", res.rector_z,
                    res.rector_noreco, res.rector_z + res.rector_noreco,
                    getattr(opt, 'tol_nivel_m', TOL_NIVEL_INFERIOR_M)))
    if res.noreco_valores:
        output.print_md("> Valores de `{}` que no corresponden a ningun nivel "
                        "(ni del modelo activo ni de las Areas):".format(param_niv))
        for v, n in sorted(res.noreco_valores.items(), key=lambda kv: -kv[1])[:10]:
            output.print_md("- `{}` x{}".format(v, n))

    tot_niv = res.niv_ok + res.niv_mal
    if tot_niv:
        pct = 100.0 * res.niv_ok / tot_niv
        output.print_md(
            "**`{}` contra el nivel por Z:** {} coinciden, {} no ({:.1f}% "
            "sobre {} elementos). Donde no coinciden manda `{}`."
            .format(param_niv, res.niv_ok, res.niv_mal, pct, tot_niv, param_niv))
        for eid, dicho, calc, z in res.niv_ejemplos[:6]:
            output.print_md(
                "- {} z={:.3f} ft - `{}`=**{}** (por Z seria **{}**)".format(
                    output.linkify(eid), z, param_niv, dicho, calc))

    if res.med_familias:
        output.print_md("**Reconocido como medidor:**")
        for k in sorted(res.med_familias):
            output.print_md("- `{}` x{}".format(k, res.med_familias[k]))
    if res.tan_familias:
        output.print_md("**Reconocido como tanque / acometida:**")
        for k in sorted(res.tan_familias):
            output.print_md("- `{}` x{}".format(k, res.tan_familias[k]))

    # ------------------------------------------------------------ por depto
    por_depto = {}
    for id_n, v in res.valores.items():
        if v in (VALOR_DUPLICADO,):
            continue
        e = res.elementos.get(id_n)
        if e is None:
            continue
        d = por_depto.setdefault(v, {"n": 0, "ml": 0.0, "diam": {}})
        d["n"] += 1
        if es_tubo(e):
            L = longitud_ft(e) * FT
            d["ml"] += L
            dia = diametro_texto(e)
            d["diam"][dia] = d["diam"].get(dia, 0.0) + L

    filas = []
    for v in sorted(por_depto.keys()):
        d = por_depto[v]
        detalle = ", ".join("{}: {:.2f}".format(k, m)
                            for k, m in sorted(d["diam"].items(),
                                               key=lambda kv: -kv[1])[:4])
        filas.append([v, d["n"], round(d["ml"], 2), detalle])
    output.print_table(filas,
                       columns=["Departamento", "Elementos", "ml tuberia",
                                "ml por diametro (top 4)"],
                       title="Cuantificacion por departamento")

    # -------------------------------------------------------------- bancos
    filas_b = []
    for i, banco in enumerate(res.bancos, 1):
        deptos = []
        pend = 0
        for mid in banco:
            d = res.medidor_depto.get(mid)
            if d:
                deptos.append(d.replace("Dpto. ", ""))
            else:
                pend += 1
        filas_b.append(["Banco {:02d}".format(i), len(banco),
                        ", ".join(sorted(deptos)) if deptos else "-",
                        pend])
    output.print_table(filas_b,
                       columns=["Banco", "Medidores", "Deptos resueltos",
                                "Sin resolver"],
                       title="Bancos de medidores")

    # ------------------------------------------------------------- alertas
    hay_alertas = bool(res.ramales_pend or res.cruces or res.ambiguos
                       or res.alertas_serie or res.dups or res.sin_param)
    output.print_md("## Revisar")
    if not hay_alertas:
        output.print_md("Nada que reportar. La red quedo completa y coherente.")
        return

    if res.ramales_pend:
        _unicos = []
        _vistos = set()
        for _t in res.ramales_pend:
            _k = eid_int(_t[0])
            if _k not in _vistos:
                _vistos.add(_k)
                _unicos.append(_t)
        output.print_md("### Medidores sin ramal resuelto ({} de {} trozos)".format(
            len(_unicos), len(res.ramales_pend)))
        output.print_md(
            "Su ramal no toca ningun Area de departamento. Normalmente es "
            "bajada sin modelar, sin conectar, o que termina en el aire.")
        for mid, n, niv in _unicos[:40]:
            sug = ""
            col = res.columna_de_banco.get(mid)
            piso = re.search(r"(\d+)", niv or "")
            if col and piso:
                candidato = FORMATO_DEPTO.format("{}{}".format(piso.group(1), col))
                tomados = set(v for v in res.medidor_depto.values() if v)
                if candidato not in tomados:
                    sug = " - por la serie del banco deberia ser **{}**".format(
                        candidato)
            output.print_md(
                "- Medidor {} - {} elementos, baja hasta **{}**{}".format(
                    output.linkify(mid), n, niv, sug))
        if len(_unicos) > 40:
            output.print_md("- ... y {} medidores mas".format(len(_unicos) - 40))

    for tipo, banco, datos, faltan in res.alertas_serie:
        if tipo == "falta_piso" and datos:
            col, pisos = datos[0]
            output.print_md(
                "- **Serie incompleta** en un banco de {} medidores: columna "
                "**{}**, pisos resueltos {}. Faltan {} medidor(es): {}"
                .format(len(banco), col, pisos, len(faltan),
                        ", ".join(output.linkify(m) for m in faltan[:8])))
        elif tipo == "columna_mezclada":
            output.print_md(
                "- **Columna mezclada**: un banco de {} medidores resolvio a "
                "deptos de columnas distintas ({}). Revisa si dos bajadas se "
                "cruzaron."
                .format(len(banco),
                        ", ".join(d for _, d, _ in datos[:6])))
        elif tipo == "depto_repetido":
            output.print_md(
                "- **Medidores repetidos** en un banco de {}: {}. Revisa si "
                "hay un banco duplicado encima de otro (las tablas lo van a "
                "contar doble)."
                .format(len(banco),
                        "; ".join("{} x{}".format(d, len(ids))
                                  for d, ids in datos[:6])))
        elif tipo == "sin_resolver":
            output.print_md(
                "- **Banco entero sin resolver**: {} medidores, ninguno llego "
                "a un depto ({})."
                .format(len(banco),
                        ", ".join(output.linkify(m) for m in banco[:6])))

    if res.cruces:
        output.print_md("### Posible cruce entre ramales ({})".format(len(res.cruces)))
        output.print_md(
            "Una misma red esta tocando mas de un medidor. O dos bajadas se "
            "unieron por error, o falta una valvula de corte entre ellas.")
        for _, n_med, n_el, gan in res.cruces[:20]:
            output.print_md("- {} medidores sobre una red de {} elementos, "
                            "asignada a **{}**".format(n_med, n_el, gan))

    if res.ambiguos:
        output.print_md("### Votacion reñida ({})".format(len(res.ambiguos)))
        for gan, v1, seg, v2, n in res.ambiguos[:20]:
            output.print_md("- **{}** ({} votos) contra **{}** ({} votos), "
                            "sobre {} elementos".format(gan, v1, seg, v2, n))

    if res.dups:
        output.print_md("### Duplicados marcados ({})".format(len(res.dups)))
        output.print_md(
            "Ejemplares identicos encima de otro. Se marcaron como "
            "`{}` para que los filtres fuera de la tabla; el original conserva "
            "su departamento. Borralos en Revit cuando los verifiques."
            .format(VALOR_DUPLICADO))
        output.print_md("- IDs: " + ", ".join(
            output.linkify(d) for d in res.dups[:30]))
        if len(res.dups) > 30:
            output.print_md("- ... y {} mas".format(len(res.dups) - 30))

    if res.sin_param:
        output.print_md("### Sin el parametro '{}' ({})".format(
            opt.parametro, len(res.sin_param)))
        output.print_md(
            "Estos elementos no tienen el parametro, o es de solo lectura. "
            "Agregalo como parametro de proyecto a su categoria.")
        cats = {}
        for i in res.sin_param:
            e = res.elementos.get(i)
            if e is not None and e.Category:
                cats[e.Category.Name] = cats.get(e.Category.Name, 0) + 1
        for c, n in sorted(cats.items(), key=lambda kv: -kv[1]):
            output.print_md("- {}: {}".format(c, n))

    output.print_md("### Conteo por categoria")
    for c in sorted(res.conteo_cat):
        output.print_md("- {}: {}".format(c, res.conteo_cat[c]))


# =============================================================================
#  6. EXTERNAL EVENT
# =============================================================================
# Una ventana no modal vive fuera del contexto de la API de Revit: si desde el
# boton se llama directo a una Transaction, Revit lanza
# "Starting a transaction from an external application running outside of API
# context is not allowed". El ExternalEvent es el puente: el boton solo hace
# Raise(), y Revit ejecuta el trabajo cuando le toca, ya dentro de su contexto.

class ManejadorGas(IExternalEventHandler):

    def __init__(self):
        self.ventana = None
        self.opt = None

    def Execute(self, uiapp):
        res = None
        try:
            uidoc = uiapp.ActiveUIDocument
            if uidoc is None:
                raise Exception("No hay ningun documento activo en Revit.")
            doc = uidoc.Document
            vista = uidoc.ActiveView

            res = procesar(doc, vista, self.opt)
            reportar(res, self.opt, vista if self.opt.solo_vista else None)

        except InvalidOperationException:
            # Revit ya no esta en condiciones de atender el evento.
            pass
        except Exception as ex:
            logger.error("Fallo el proceso de gas: {}".format(ex))
            if res is None:
                res = Resultado()
            res.error = str(ex)
            try:
                reportar(res, self.opt, None)
            except Exception:
                pass
        finally:
            if self.ventana is not None:
                try:
                    self.ventana.terminado(res)
                except Exception:
                    pass

    def GetName(self):
        return "Asignacion de gas por departamento"


# =============================================================================
#  7. VENTANA NO MODAL
# =============================================================================

XAML = u"""
<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="Gas por departamento"
    Height="880" Width="500"
    ResizeMode="CanResize"
    WindowStartupLocation="CenterScreen"
    ShowInTaskbar="True">
    <Grid Margin="14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0">
            <TextBlock Text="Corta la red en el medidor y asigna cada ramal a su departamento."
                       TextWrapping="Wrap" FontSize="12" Margin="0,0,0,10"/>
        </StackPanel>

        <StackPanel Grid.Row="1">
            <TextBlock Text="Parametro donde se escribe el departamento" FontWeight="Bold"/>
            <TextBox x:Name="tb_param" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Tolerancia para unir puntas sueltas (m). 0 = solo conectores reales."
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_tol" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Tolerancia de NIVEL por Z (m), solo para elementos sin nivel rector. 0.005 si la tuberia va por el techo (gas); 0.15 si va por el piso."
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_tolniv" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Parametro con el NIVEL RECTOR de cada elemento (vacio = nivel por Z)"
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_pniv" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="De donde leer las Areas" FontWeight="Bold"/>
            <CheckBox x:Name="cb_ahost" Content="Areas del modelo activo"
                      Margin="0,3,0,3"/>
            <CheckBox x:Name="cb_alink" Content="Areas de los modelos vinculados (cada uno las suyas)"
                      Margin="0,0,0,3"/>
            <TextBlock Text="Solo vinculos cuyo nombre contenga (vacio = todos)"
                       TextWrapping="Wrap"/>
            <TextBox x:Name="tb_link" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Palabras que identifican un MEDIDOR (familia o tipo), separadas por coma"
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_med" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Palabras que VETAN un medidor (valvulas que se llaman medidor)"
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_veto" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Palabras que identifican el TANQUE / regulacion de acometida"
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_tan" Height="24" Margin="0,3,0,10"/>

            <TextBlock Text="Filtrar por subproyecto que contenga (vacio = no filtrar)"
                       TextWrapping="Wrap" FontWeight="Bold"/>
            <TextBox x:Name="tb_ws" Height="24" Margin="0,3,0,10"/>

            <CheckBox x:Name="cb_vista" Content="Solo los elementos de la vista activa"
                      Margin="0,0,0,5"/>
            <CheckBox x:Name="cb_dups" Content="Marcar ejemplares duplicados en la misma ubicacion"
                      Margin="0,0,0,10"/>
        </StackPanel>

        <Border Grid.Row="2" BorderBrush="#CCCCCC" BorderThickness="1" Margin="0,4,0,10">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
                <TextBlock x:Name="tb_log" Margin="8" TextWrapping="Wrap"
                           FontFamily="Consolas" FontSize="11"
                           Text="Listo. Empieza por Diagnosticar: calcula todo y te ensena el reporte sin tocar el modelo."/>
            </ScrollViewer>
        </Border>

        <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right">
            <Button x:Name="btn_diag" Content="Diagnosticar" Width="120" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_asig" Content="Asignar" Width="120" Height="30" Margin="0,0,8,0"/>
            <Button x:Name="btn_cerrar" Content="Cerrar" Width="90" Height="30"/>
        </StackPanel>
    </Grid>
</Window>
"""


class VentanaGas(forms.WPFWindow):

    def __init__(self, manejador, evento, opt_inicial):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.manejador = manejador
        self.evento = evento
        self.manejador.ventana = self

        self.tb_param.Text = opt_inicial.parametro
        self.tb_tol.Text = "{:.3f}".format(opt_inicial.tolerancia_m)
        self.tb_tolniv.Text = "{:.3f}".format(opt_inicial.tol_nivel_m)
        self.tb_pniv.Text = opt_inicial.param_nivel
        self.cb_ahost.IsChecked = opt_inicial.areas_host
        self.cb_alink.IsChecked = opt_inicial.areas_vinculos
        self.tb_link.Text = opt_inicial.filtro_vinculo
        self.tb_med.Text = opt_inicial.claves_medidor
        self.tb_veto.Text = opt_inicial.claves_veto
        self.tb_tan.Text = opt_inicial.claves_tanque
        self.tb_ws.Text = opt_inicial.filtro_workset
        self.cb_vista.IsChecked = opt_inicial.solo_vista
        self.cb_dups.IsChecked = opt_inicial.marcar_dups

        self.btn_diag.Click += self.on_diagnosticar
        self.btn_asig.Click += self.on_asignar
        self.btn_cerrar.Click += self.on_cerrar

    # -- helpers ---------------------------------------------------------------

    def leer_opciones(self, escribir):
        opt = Opciones()
        opt.parametro = (self.tb_param.Text or "").strip() or "Area/dpto"
        try:
            opt.tolerancia_m = float((self.tb_tol.Text or "0").replace(",", "."))
        except ValueError:
            opt.tolerancia_m = 0.0
        try:
            opt.tol_nivel_m = float((self.tb_tolniv.Text or "0.005").replace(",", "."))
        except ValueError:
            opt.tol_nivel_m = TOL_NIVEL_INFERIOR_M
        # Vacio es valido: desactiva el nivel rector y todo va por Z.
        opt.param_nivel = (self.tb_pniv.Text or "").strip()
        opt.areas_host = bool(self.cb_ahost.IsChecked)
        opt.areas_vinculos = bool(self.cb_alink.IsChecked)
        opt.filtro_vinculo = (self.tb_link.Text or "").strip()
        opt.claves_medidor = self.tb_med.Text or CLAVES_MEDIDOR_DEF
        opt.claves_veto = self.tb_veto.Text or CLAVES_VETO_DEF
        opt.claves_tanque = self.tb_tan.Text or CLAVES_TANQUE_DEF
        opt.filtro_workset = (self.tb_ws.Text or "").strip()
        opt.solo_vista = bool(self.cb_vista.IsChecked)
        opt.marcar_dups = bool(self.cb_dups.IsChecked)
        opt.escribir = escribir
        return opt

    def guardar_config(self, opt):
        try:
            cfg.param_dpto = opt.parametro
            cfg.tol_tubos = str(opt.tolerancia_m)
            cfg.tol_nivel = str(opt.tol_nivel_m)
            cfg.param_nivel = opt.param_nivel
            cfg.areas_host = opt.areas_host
            cfg.areas_vinculos = opt.areas_vinculos
            cfg.filtro_vinculo = opt.filtro_vinculo
            cfg.claves_medidor = opt.claves_medidor
            cfg.claves_veto = opt.claves_veto
            cfg.claves_tanque = opt.claves_tanque
            cfg.filtro_workset = opt.filtro_workset
            cfg.solo_vista = opt.solo_vista
            cfg.marcar_dups = opt.marcar_dups
            script.save_config()
        except Exception as ex:
            logger.debug("No se pudo guardar la configuracion: {}".format(ex))

    def log(self, texto):
        self.tb_log.Text = texto

    def ocupado(self, si):
        self.btn_diag.IsEnabled = not si
        self.btn_asig.IsEnabled = not si

    def lanzar(self, escribir):
        opt = self.leer_opciones(escribir)
        self.guardar_config(opt)
        self.manejador.opt = opt
        self.ocupado(True)
        self.log("Procesando... el reporte se abre en la ventana de salida de pyRevit.")
        self.evento.Raise()

    # -- eventos ---------------------------------------------------------------

    def on_diagnosticar(self, sender, args):
        self.lanzar(False)

    def on_asignar(self, sender, args):
        confirmar = forms.alert(
            "Se va a escribir el parametro '{}' en todos los elementos de gas "
            "del alcance elegido.\n\nEsto sobrescribe lo que haya hoy. "
            "Se puede deshacer con Ctrl+Z.\n\n Continuar?"
            .format((self.tb_param.Text or "").strip()),
            title="Confirmar escritura", yes=True, no=True)
        if not confirmar:
            return
        self.lanzar(True)

    def on_cerrar(self, sender, args):
        self.Close()

    # -- callback desde el ExternalEvent --------------------------------------

    def terminado(self, res):
        self.ocupado(False)
        if res is None:
            self.log("El proceso termino sin resultado. Revisa el log de pyRevit.")
            return
        if res.error:
            self.log("No se pudo completar:\n\n{}".format(res.error))
            return

        n_dep = sum(1 for v in res.valores.values()
                    if v not in (VALOR_COMUN, VALOR_SIN, VALOR_DUPLICADO))
        n_sin = sum(1 for v in res.valores.values() if v == VALOR_SIN)
        n_com = sum(1 for v in res.valores.values() if v == VALOR_COMUN)
        pend = len(res.ramales_pend)

        lineas = [
            "Elementos en el alcance : {}".format(len(res.elementos)),
            "Medidores / bancos      : {} / {}".format(len(res.medidores),
                                                       len(res.bancos)),
            "Ramales resueltos       : {}".format(res.ramales_ok),
            "Con departamento        : {}".format(n_dep),
            "Red general gas         : {}".format(n_com),
            "Sin asignar             : {}".format(n_sin),
            "Duplicados marcados     : {}".format(len(res.dups)),
            "Parametros escritos     : {}".format(res.n_escritos),
            "",
            "Uniones por conector    : {}".format(res.aristas_conector),
            "Uniones por tolerancia  : {}".format(res.aristas_toler),
            "",
            "Nivel de Area Cuantif   : {}".format(res.rector_param),
            "Nivel por Z (respaldo)  : {}".format(res.rector_z + res.rector_noreco),
            "Fuentes de Areas        : {}".format(
                ", ".join("{} ({})".format(o, d) for o, _, d in res.fuentes_areas)
                or "-"),
        ]
        if res.vinculos_descargados:
            lineas.append("OJO: vinculos descargados: {}".format(
                ", ".join(res.vinculos_descargados)))
        if res.medidores and res.med_sin_conn >= 0.5 * len(res.medidores):
            lineas.append("")
            lineas.append("OJO: {} de {} medidores no tienen conectores MEP."
                          .format(res.med_sin_conn, len(res.medidores)))
            lineas.append("El corte en el medidor no se pudo aplicar; se uso")
            lineas.append("el camino alterno. Lee el reporte.")
        if pend:
            lineas.append("")
            lineas.append("OJO: {} medidor(es) sin ramal resuelto. "
                          "El reporte te dice cuales.".format(pend))
        if res.cruces:
            lineas.append("OJO: {} posible(s) cruce(s) entre ramales.".format(
                len(res.cruces)))
        lineas.append("")
        lineas.append("El detalle completo esta en la ventana de salida.")
        self.log("\n".join(lineas))


# =============================================================================
#  8. ARRANQUE
# =============================================================================

def opciones_guardadas():
    opt = Opciones()
    # SHIFT + clic vuelve a los valores de fabrica, igual que en tu script de
    # hidraulica.
    if globals().get('__shiftclick__', False):
        return opt
    opt.parametro      = getattr(cfg, 'param_dpto', opt.parametro)
    try:
        opt.tolerancia_m = float(getattr(cfg, 'tol_tubos', opt.tolerancia_m))
    except (TypeError, ValueError):
        pass
    try:
        opt.tol_nivel_m = float(getattr(cfg, 'tol_nivel', opt.tol_nivel_m))
    except (TypeError, ValueError):
        pass
    opt.param_nivel    = getattr(cfg, 'param_nivel', opt.param_nivel)
    opt.areas_host     = bool(getattr(cfg, 'areas_host', opt.areas_host))
    opt.areas_vinculos = bool(getattr(cfg, 'areas_vinculos', opt.areas_vinculos))
    opt.filtro_vinculo = getattr(cfg, 'filtro_vinculo', opt.filtro_vinculo)
    opt.claves_medidor = getattr(cfg, 'claves_medidor', opt.claves_medidor)
    opt.claves_veto    = getattr(cfg, 'claves_veto', opt.claves_veto)
    opt.claves_tanque  = getattr(cfg, 'claves_tanque', opt.claves_tanque)
    opt.filtro_workset = getattr(cfg, 'filtro_workset', opt.filtro_workset)
    opt.solo_vista     = bool(getattr(cfg, 'solo_vista', opt.solo_vista))
    opt.marcar_dups    = bool(getattr(cfg, 'marcar_dups', opt.marcar_dups))
    return opt


def main():
    if revit.doc is None:
        forms.alert("Abre un proyecto de Revit antes de correr esto.",
                    title="Sin documento", warn_icon=True)
        return

    manejador = ManejadorGas()
    evento = ExternalEvent.Create(manejador)

    ventana = VentanaGas(manejador, evento, opciones_guardadas())
    # show() = no modal: la ventana queda abierta y Revit sigue usable.
    ventana.show()


main()