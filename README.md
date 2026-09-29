# Vigía — Videovigilancia inteligente con alertas por modalidades

[![Pruebas](https://github.com/enri-escaray/vigia/actions/workflows/pruebas.yml/badge.svg)](https://github.com/enri-escaray/vigia/actions/workflows/pruebas.yml)

Vigía analiza en tiempo real el video de tus cámaras (webcam, cámaras IP por
RTSP, transmisiones HLS/HTTP o archivos de video), **detecta comportamientos
indebidos o sospechosos** y **dispara alertas** con captura, clip de video y
notificaciones. Qué se considera sospechoso depende de la **modalidad** activa
(En casa, Nocturno, Ausente, Comercio abierto, Vía pública…), que puede
cambiarse a mano o automáticamente según un horario.

Todo corre en tu equipo: no depende de servicios en la nube y no hace
reconocimiento facial.

---

## Índice

1. [Qué detecta](#1-qué-detecta)
2. [Modalidades](#2-modalidades)
3. [Instalación](#3-instalación)
4. [Primeros pasos](#4-primeros-pasos)
5. [Probarlo con cámaras reales: APIs y fuentes públicas](#5-probarlo-con-cámaras-reales-apis-y-fuentes-públicas)
6. [El panel web](#6-el-panel-web)
7. [Configuración (`config.yaml`)](#7-configuración-configyaml)
8. [Referencia de reglas](#8-referencia-de-reglas)
9. [Notificaciones](#9-notificaciones)
10. [Arquitectura](#10-arquitectura)
11. [API REST](#11-api-rest)
12. [Comandos](#12-comandos)
13. [Rendimiento](#13-rendimiento)
14. [Limitaciones, privacidad y uso responsable](#14-limitaciones-privacidad-y-uso-responsable)
15. [Pruebas automáticas](#15-pruebas-automáticas)
16. [Solución de problemas](#16-solución-de-problemas)

---

## 1. Qué detecta

| Regla (`tipo`) | Qué se considera sospechoso | Ejemplo de uso |
|---|---|---|
| `intrusion` | Alguien entra a una **zona restringida** | Bodega, caja, patio de noche |
| `presencia` | **Cualquier** persona visible | Casa vacía, local cerrado |
| `merodeo` | Alguien **permanece demasiado** en una zona | Merodeo en la puerta, vehículo detenido |
| `cruce_linea` | Se **cruza una línea virtual** en un sentido | Acceso de entrada, reja, contramano |
| `objeto_abandonado` | Un bolso/maleta queda **quieto y sin dueño** | Paquete sospechoso |
| `objeto_peligroso` | Se ve un **cuchillo, bate** u otra arma del modelo | Asalto |
| `aglomeracion` | Demasiadas personas (o vehículos) en un lugar | Tumulto, congestión |
| `carrera` | Movimiento **anormalmente rápido** | Huida tras un robo |
| `caida` | Una persona **cae y no se levanta** | Accidente, desmayo |
| `altercado` | Personas juntas con **movimiento brusco** sostenido *(experimental)* | Pelea |
| `sabotaje` | Cámara **tapada, desenfocada, girada, a oscuras o sin señal** | Vandalismo, corte de cable |

Cada alerta guarda **una captura** con el objeto marcado en rojo, **un clip de
video** con los segundos previos y posteriores, y queda registrada en el
historial. Las alertas tienen **severidad** (baja, media, alta, crítica) y un
**enfriamiento** para no repetir el mismo aviso.

## 2. Modalidades

Una modalidad es un conjunto de reglas con su severidad. El `config.yaml`
incluido trae estas:

| Modalidad | Pensada para | Reglas principales |
|---|---|---|
| **Desarmado** | Sistema en reposo | Solo sabotaje |
| **En casa** (`hogar`) | Hay gente adentro | Intrusión en zona restringida, merodeo en la entrada, armas, caídas, sabotaje |
| **Nocturno** | De noche | Intrusión (crítica), cruce del acceso, merodeo corto, vehículo detenido, armas |
| **Ausente** | Nadie debería estar | Cualquier presencia es crítica, armas, sabotaje |
| **Comercio abierto** | Horario de atención | Merodeo, aglomeración, objetos abandonados, armas, carreras, caídas, peleas |
| **Vía pública** (`transito`) | Cámaras de autopista | Peatón en la autopista, vehículo detenido en la banquina, contramano, congestión |
| **Espacio público** (`espacio_publico`) | Calles y plazas | Tumultos, personas corriendo, permanencias largas, objetos abandonados o peligrosos, vehículo detenido en el cruce |

- **Automático:** la modalidad cambia sola según el `horario` (por ejemplo,
  *Nocturno* de 23:00 a 06:00 y *En casa* el resto del día).
- **Manual:** se elige desde el panel y queda fija hasta volver a
  "Automático". La elección se guarda en `datos/estado.json`, así que un corte
  de luz no desarma el sistema.

- **Modalidad fija por cámara:** una cámara puede usar siempre la misma
  modalidad (`modalidad: transito` en su configuración), sin importar la
  general. El `config.yaml` incluido lo usa para vigilar al mismo tiempo tu
  webcam, que sigue la modalidad general, y dos cámaras públicas de
  California: una autopista en *Vía pública* y un cruce urbano en *Espacio
  público*. Las modalidades que solo usan esas cámaras no aparecen en el
  selector general, y cada cámara muestra la suya en su recuadro.

Puedes crear tus propias modalidades combinando reglas (ver sección 7).

## 3. Instalación

Requisitos: **Python 3.10 o superior**. Una GPU NVIDIA es opcional.

```bash
python -m venv .venv
```

En Windows (PowerShell):

```bash
.venv\Scripts\Activate.ps1
```

En Linux/macOS:

```bash
source .venv/bin/activate
```

Y luego:

```bash
pip install -r requirements.txt
```

Esto instala OpenCV, FastAPI, Ultralytics (YOLO) y PyTorch. El modelo YOLO
(`modelos/yolo11n.pt`, unos 6 MB) se descarga solo la primera vez que se usa.

> Si no quieres instalar PyTorch, quita `ultralytics` de `requirements.txt`:
> Vigía usará el detector HOG (solo con OpenCV 4) o el de movimiento.

## 4. Primeros pasos

**a) Demostración sin cámaras.** Genera un video sintético con un cruce de
línea, una intrusión con merodeo, alguien que corre y una cámara tapada:

```bash
python -m vigia demo --abrir
```

**b) Con tu webcam.** El `config.yaml` ya viene preparado para la webcam 0:

```bash
python -m vigia iniciar --abrir
```

Luego abre **Zonas y líneas** en el panel y dibuja las zonas sobre tu imagen.
Para saber qué número tiene cada webcam: `python -m vigia camaras`.

**c) Con cámaras públicas en vivo** (sección siguiente):

```bash
python -m vigia publicas --iniciar --abrir
```

El panel queda en <http://localhost:8080> (la demo y las cámaras públicas usan
el puerto que indiques con `--puerto`).

## 5. Probarlo con cámaras reales: APIs y fuentes públicas

Para probar Vigía con video real sin tener cámaras propias, se evaluaron varias
APIs y fuentes públicas:

| Fuente | Qué ofrece | Clave | ¿Sirve para Vigía? |
|---|---|---|---|
| **[Caltrans CWWP2](https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm)** (California) | JSON por distrito con cientos de cámaras de autopista y su **video en vivo HLS** | No, gratis | ✅ **Integrada** (`python -m vigia publicas`) |
| [Nevada 511](https://www.nvroads.com/help/endpoint/cameras) | Cámaras con video HLS (`.m3u8`) | Sí, gratis (registro) | ✅ Copiar la URL `.m3u8` en `fuente` |
| [NYC DOT](https://webcams.nyctmc.org/cameras-list) (Nueva York) | JSON con imágenes JPEG de 352×240 cada ~2 s | No | ⚠️ Solo fotos: poco útil para seguimiento |
| [Windy Webcams API](https://api.windy.com/webcams/docs) | Webcams del mundo | Sí (plan gratis) | ⚠️ En el plan gratis son imágenes de baja resolución y *timelapse*, no video en vivo |
| [NSW Live Traffic Cameras](https://data.nsw.gov.au/data/dataset/2-live-traffic-cameras/resource/a4346b73-21be-4df7-990e-55022ae5d71e) (Australia) | Imágenes y ubicación en GeoJSON | Según el portal | ⚠️ Solo imágenes |
| [traffic-camera-sources](https://github.com/bzsasson/traffic-camera-sources) | Registro abierto de las APIs oficiales de cámaras de tránsito de EE. UU. | — | 📚 Para buscar más fuentes |

### Caltrans: lo que se probó

`python -m vigia publicas` consulta la API de Caltrans, **verifica qué
transmisiones están activas en este momento** (muchas figuran en servicio pero
no transmiten) y genera `config.publico.yaml` con la modalidad **Vía pública**:

```bash
python -m vigia publicas --distrito 4 --cantidad 3 --crear config.publico.yaml
```

```bash
python -m vigia iniciar -c config.publico.yaml --abrir
```

Distritos útiles: `4` San Francisco, `7` Los Ángeles, `11` San Diego, `12`
Orange County, `3` Sacramento. Con `--buscar "San Francisco"` se filtra por
nombre o lugar.

**Resultado de la prueba real** (22/09/2026, tres cámaras del Área de la Bahía,
en un portátil sin GPU):

- YOLO siguió **unos 20 a 25 vehículos a la vez** en la I-80 (Emeryville) a
  unos 7 cuadros por segundo por cámara, con las tres cámaras funcionando a la vez.
- Se disparó una alerta real de **Congestión** («18 vehículos, umbral 12»), con
  captura y clip.
- Se disparó una alerta real de **Cámara obstruida**: la cámara de la I-280
  en San Francisco empezó a transmitir una imagen gris uniforme. Minutos
  después esa transmisión se cortó y llegó la alerta **Cámara sin señal**; el
  sistema siguió reintentando hasta que la cámara volvió.
- La prueba sirvió para corregir cuatro problemas que solo aparecen con cámaras
  reales: falsas alertas de «sin señal» mientras cargaba el modelo, cortes
  aparentes porque HLS entrega el video en ráfagas, textos superpuestos en
  imágenes pequeñas y un consumo de CPU excesivo con varias cámaras (ver
  sección 13).

**Segunda prueba, de día** (23/09/2026, autopista I-80 y el cruce de Ocean
Blvd en Long Beach). Se grabaron 3 minutos de cada cámara y se repitieron por
el sistema para medir cada ajuste con los mismos cuadros:

| Problema encontrado | Antes | Después |
|---|---|---|
| El cartel verde de la autopista marcado como «vehículo» (el seguimiento saltaba de un auto al cartel) | 94 cuadros en 3 min | destellos aislados de 1 cuadro |
| Autos cercanos y rápidos sin marcar (detecciones seguras que terminan marcadas) | 59 % | 79 % |
| Cruce: camiones vistos desde arriba sin marcar (vehículos marcados por cuadro) | 0,2 | 1,9 (3,2 en vivo) |
| Transmisión cortada: tiempo hasta reconectar | 80 s | unos 15 s |
| Clip de la alerta «sin señal» | 86 s (el máximo es 30) | los últimos segundos antes del corte |

En el cruce, el modelo `yolo11n` casi no reconoce camiones vistos desde
arriba: esa cámara usa `yolo11s` (3 veces más detecciones, el doble de CPU) y
muestra los vehículos quietos desde un 35 % de confianza, porque ahí esperan
el semáforo.

Para aprovechar las reglas de **banquina** (vehículo detenido) y **contramano**,
dibuja en el panel una zona llamada `banquina` y una línea llamada `sentido`
cuya flecha apunte en el sentido de circulación: cruzarla al revés dispara la
alerta crítica «Vehículo en contramano».

### Videos de prueba para comportamientos específicos

Las autopistas no suelen mostrar peleas ni bolsos abandonados. Para eso hay
bases de datos de video de investigación que se usan como archivo (`fuente:
ruta/al/video.mpg`):

- **[CAVIAR](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/)**: escenas actuadas
  de personas caminando, reuniéndose, **peleando**, **desmayándose** y **dejando
  un paquete**. Son archivos MPEG2 de 384×288. Si publicas resultados, hay que
  citar el proyecto CAVIAR (EC IST 2001 37540).
- **[UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/)**: 1.900 videos
  reales de cámaras de seguridad con robos, peleas, vandalismo, accidentes,
  etc.
- **PETS 2006**: estación de tren con **equipaje abandonado** (muy usada en
  investigación, por ejemplo en [este trabajo](https://arxiv.org/pdf/1803.01160)).

> ❌ **No uses** sitios que muestran cámaras privadas mal protegidas (tipo
> "Insecam") ni cámaras halladas con buscadores como Shodan: ver cámaras
> ajenas sin permiso es ilegal y viola la privacidad de otras personas.

## 6. El panel web

- **Barra superior:** modalidad (Automático o una modalidad fija), cámaras en
  línea, alertas pendientes, sonido en el navegador y ⚡ *alerta de prueba*.
- **Cámaras:** video en vivo con las zonas (se tiñen de rojo si están
  ocupadas), las líneas con su flecha de entrada, los objetos seguidos con su
  número, y un borde rojo con el título cuando hay una alerta. Abajo se ven la
  **integridad** de la cámara y las reglas activas. ⛶ pasa a pantalla completa.
- **Alertas:** llegan al instante (con sonido y aviso emergente). Se pueden
  filtrar por severidad o por pendientes. Al abrir una se ven la captura, el
  clip y los detalles, y se puede **reconocer**.
- **Zonas y líneas:** el editor sobre la imagen real de la cámara:
  - *Nueva zona*: clic en cada vértice y luego doble clic, clic en el primer
    punto, Enter o *Terminar zona*.
  - *Nueva línea*: clic en el inicio y en el fin. ⇄ invierte el sentido.
  - Arrastra los vértices para ajustarlos. Supr borra el elemento elegido.
  - **Guardar** aplica los cambios al instante y los escribe en el YAML: solo
    se tocan las líneas de esas zonas y el resto del archivo, incluidos los
    comentarios, queda igual. Se deja una copia en `config.yaml.bak`.

Las zonas se evalúan con el **punto de apoyo** de cada objeto (el centro de
sus pies), así que conviene dibujarlas sobre el piso.

**Zonas de exclusión:** si una zona se llama `ignorar...` (por ejemplo
`ignorar_cartel`), se descarta todo lo que el detector encuentre dentro. Se
dibujan en gris. Sirven para tapar carteles, pantallas, afiches o reflejos que
el modelo confunde con personas o vehículos.

## 7. Configuración (`config.yaml`)

El archivo está comentado y en español. Las coordenadas van de 0 a 1
(fracción del ancho y del alto), con el origen arriba a la izquierda. Se pueden
usar variables de entorno con `${NOMBRE}` o `${NOMBRE:-por_defecto}`; si hay un
`.env` junto al YAML se carga solo (ver `.env.example`).

### Cámaras

```yaml
camaras:
  - id: patio                 # letras, números, _ o -
    nombre: "Patio trasero"
    fuente: "rtsp://${CAM_USUARIO}:${CAM_CLAVE}@192.168.1.64:554/Streaming/Channels/101"
    fps: 10                   # cuadros analizados por segundo
    fps_deteccion: 5          # detecciones por segundo (más = más CPU)
    fps_reposo: 1             # si la escena está quieta y vacía (0 = desactivado)
    ancho_max: 1280           # se reduce la imagen si es más grande
    repetir: true             # solo archivos: volver a empezar al terminar
    rtsp_tcp: true            # RTSP por TCP (más estable)
    modalidad: transito       # opcional: modalidad fija (si no, sigue la general)
    deteccion:                # opcional: ajustes de detección solo para esta cámara
      confianza: 0.25         # p. ej. más baja para cámaras nocturnas o lejanas
      modelo: modelos/yolo11s.pt   # un modelo más grande solo donde hace falta
    seguimiento:              # opcional: ajustes de seguimiento solo para esta cámara
      confianza_quietos: 0.35 # p. ej. en un cruce, donde los vehículos esperan el semáforo
    activo: true
    zonas:
      - nombre: puerta
        puntos: [[0.1, 0.5], [0.4, 0.5], [0.4, 0.95], [0.1, 0.95]]
    lineas:
      - nombre: reja
        puntos: [[0.5, 0.95], [0.5, 0.2]]   # entrada = de izquierda a derecha de A->B
```

`fuente` puede ser: un número de webcam (`0`), `rtsp://…`, `http(s)://…`
(MJPEG o HLS `.m3u8`) o la ruta de un archivo de video.

### Detección y seguimiento

```yaml
deteccion:
  tipo: auto            # auto | yolo | hog | movimiento
  modelo: modelos/yolo11n.pt
  confianza: 0.40
  tamano_imagen: 640
  dispositivo: ""       # "" automático, "cpu", "cuda:0"
  hilos: 0              # hilos de cálculo por inferencia (0 = automático: núcleos / 4)
  confianza_por_categoria: {arma: 0.5}
  categorias:           # clases del modelo -> categorías de Vigía
    arma: [knife, "baseball bat"]
seguimiento:
  confirmaciones: 3     # detecciones para confirmar un objeto (evita falsos positivos)
  confirmaciones_por_categoria: {vehiculo: 2}    # los autos cruzan rápido la imagen
  edad_max: 2.0         # segundos que se recuerda un objeto que dejó de verse
  edad_max_por_categoria: {equipaje: 10}
  distancia_max_por_categoria: {vehiculo: 2.5}   # los autos rápidos avanzan más que su altura entre detecciones
  solo_en_movimiento: [vehiculo]   # un "vehículo" que nunca se movió (cartel, baliza) no se muestra ni cuenta...
  confianza_quietos: 0.5           # ...salvo que el modelo lo vea con esta confianza media (un auto estacionado)
  descartar_fijos_tras: 30         # lo quieto y dudoso visto 30 s en el mismo lugar pasa a ser "decorado"
```

Cada cámara puede redefinir parte de la detección y del seguimiento en sus
propios bloques `deteccion` y `seguimiento` (por ejemplo, la `confianza` o el
`modelo`). Lo que no indique se toma de la configuración general.

Cómo se evita que un cartel o una baliza aparezcan como vehículos (todo lo
medido con grabaciones reales de Caltrans):

- Un vehículo se muestra cuando **se movió** (media altura, en dos detecciones
  seguidas) o cuando el modelo está **bastante seguro** (`confianza_quietos`).
  Un cartel "tiembla" menos de 0,15 alturas y el modelo lo ve con un 25-45 %.
- Si el seguimiento **salta** de un auto a algo quieto (un auto que se pierde
  bajo un puente y "cae" sobre el cartel de al lado), se nota porque un auto
  real no frena de golpe: desde ahí se trata como un objeto quieto.
- Los lugares donde el modelo ve algo quieto y dudoso quedan **fijados**:
  ningún objeto en movimiento puede saltar a ellos. Si se siguen viendo 30 s
  (aunque sea a ratos), pasan a ser **decorado** y se descartan.
- Un carril por el que pasan autos nunca se aprende como decorado.
- Para casos puntuales: una zona llamada `ignorar...` descarta todo lo que se
  detecte dentro.

Las categorías por defecto son `persona`, `vehiculo` (bicicleta, auto, moto,
bus, camión), `equipaje` (mochila, bolso, maleta), `arma` (cuchillo, bate) y
`animal`. Con el detector de movimiento la única categoría es `movimiento`.
Las tijeras no cuentan como arma para evitar falsas alarmas en peluquerías y
cocinas. Para detectar **armas de fuego** hace falta un modelo YOLO entrenado
para eso: indica su archivo en `modelo` y agrega sus clases en `categorias.arma`.

### Modalidades y horario

```yaml
modalidades:
  inicial: auto                 # auto o el nombre de una modalidad
  por_defecto: hogar            # la que rige fuera de los tramos del horario
  horario:
    - modalidad: comercio
      dias: lun-sab             # todos, laborables, fin_de_semana, lun, mar, ..., vie-lun
      desde: "09:00"
      hasta: "20:00"
    - modalidad: nocturno
      desde: "23:00"
      hasta: "06:00"            # los tramos pueden cruzar la medianoche
  definiciones:
    comercio:
      etiqueta: "Comercio abierto"
      descripcion: "Horario de atención"
      color: "#f59e0b"
      reglas:
        - tipo: merodeo
          zonas: [caja]
          segundos: 60
          severidad: media
```

Si una regla menciona `zonas`, solo se aplica en las cámaras que tienen alguna
de esas zonas; sin `zonas`, vigila toda la imagen. `python -m vigia validar`
muestra qué reglas quedan activas en cada cámara y avisa de zonas mal escritas.

### Grabación, sistema y web

```yaml
grabacion: {clips: true, segundos_antes: 5, segundos_despues: 5, duracion_max: 30, calidad_jpeg: 80, ancho_clip: 960, espacio_max_gb: 5}
sistema:   {nombre: "Vigía", datos: datos, retencion_dias: 30, nivel_log: INFO}
web:       {host: 127.0.0.1, puerto: 8080, usuario: "", clave: ""}
```

- `retencion_dias`: se borran automáticamente las alertas y evidencias más viejas.
- `espacio_max_gb`: igual que un grabador (DVR), si los clips superan ese
  espacio se borran los más viejos. El codificador H.264 de Windows usa una
  tasa fija de alrededor de 1 bit por píxel y cuadro, así que un clip de 10 s
  a 960 px y 10 fps ocupa unos 6,5 MB. Para ahorrar espacio, baja `ancho_clip`.
- `web.host: 0.0.0.0` permite ver el panel desde el celular u otros equipos de
  la red. En ese caso **define `usuario` y `clave`**: el panel pedirá contraseña.

## 8. Referencia de reglas

Claves comunes a todas: `tipo`, `nombre` (id único dentro de la modalidad),
`titulo` (texto de la alerta, opcional), `severidad` (`baja`, `media`, `alta`
o `critica`), `enfriamiento` (segundos sin repetir la misma alerta),
`camaras` (limitar a ciertas cámaras), `zonas` y `activo`.

| `tipo` | Parámetros (valor por defecto) | Severidad / enfriamiento por defecto |
|---|---|---|
| `intrusion` | `categorias` [persona], `segundos_min` 0.5 | alta / 30 s |
| `presencia` | `categorias` [persona], `segundos_min` 1.0 | crítica / 60 s |
| `merodeo` | `categorias` [persona], `segundos` 30, `tolerancia` 3 (s fuera de la zona sin reiniciar), `requiere_movimiento` false (solo objetos que llegaron moviéndose) | media / 120 s |
| `cruce_linea` | `categorias` [persona], `lineas` [todas], `direccion` ambas \| entrada \| salida | alta / 10 s |
| `objeto_abandonado` | `categorias` [equipaje], `categorias_dueno` [persona], `segundos` 30, `distancia_dueno` 1.5 (alturas de persona), `movimiento_max` 0.5 (alturas del objeto), `requiere_dueno` true | alta / 300 s |
| `objeto_peligroso` | `categorias` [arma], `confianza_min` 0.5, `detecciones_min` 3 | crítica / 60 s |
| `aglomeracion` | `categorias` [persona], `umbral` 5, `segundos` 5 | media / 180 s |
| `carrera` | `categorias` [persona], `velocidad` 1.5 (cuerpos por segundo), `segundos` 0.6 | media / 60 s |
| `caida` | `categorias` [persona], `proporcion` 1.2 (ancho/alto para "tendido"), `segundos` 2, `ventana_de_pie` 5 | alta / 120 s |
| `altercado` | `categorias` [persona], `proximidad` 0.8, `energia` 0.18, `desplazamiento_max` 0.8, `ventana` 2, `segundos` 1.5 | alta / 120 s |
| `sabotaje` | `detectar` [obstruida, desenfocada, movida, oscura, sin_senal], `segundos_sin_senal` 10 | alta / 300 s |

Cómo decide cada una:

- **Velocidades y distancias en "alturas del cuerpo":** así la regla funciona
  igual si la persona está cerca o lejos de la cámara.
- **Objeto abandonado:** el objeto tuvo que estar junto a una persona en algún
  momento (`requiere_dueno`). Así no alerta por muebles que el modelo confunda
  con maletas.
- **Caída:** la persona debe haber estado de pie poco antes. Alguien que ya
  aparece acostado (por ejemplo, en un sofá) no dispara la alerta.
- **Altercado (experimental):** dos personas muy juntas, casi sin desplazarse y
  con mucho movimiento entre ellas. Puede confundirse con un abrazo efusivo;
  úsalo con severidad moderada.
- **Sabotaje:** al arrancar, cada cámara aprende su brillo, contraste, nitidez
  y mapa de bordes, y luego detecta cambios bruscos que duran más de 3 s (10 s
  en el caso de «cámara movida»). Para no confundir a alguien muy cerca del
  lente (algo típico con una webcam) con una cámara girada, en esa comparación
  se ignoran las zonas donde hay personas u objetos detectados. Si alguien
  reubica la cámara a propósito, después de 60 s la nueva vista se toma como
  referencia.

## 9. Notificaciones

El panel web siempre muestra todo. Además, con `severidad_minima` general o
por canal:

- **sonido:** pitidos en el equipo donde corre Vigía (el patrón depende de la severidad).
- **telegram:** mensaje con la foto. Crea un bot con @BotFather y obtén tu
  `chat_id` con @userinfobot.
- **webhook:** envía un POST con JSON que incluye `text` y `content`, así que
  funciona directo con **Slack**, **Discord**, n8n, Home Assistant, etc.
  `url_panel` convierte las rutas de captura y clip en enlaces completos.
- **correo:** con la captura adjunta. En Gmail usa una "contraseña de aplicación".

Los secretos van en `.env`; para empezar, copia `.env.example`. Para probar
todos los canales:

```bash
python -m vigia probar-alerta
```

## 10. Arquitectura

```
 Fuentes: webcam · RTSP · HLS/HTTP · archivo
        │   VideoSource: un hilo por cámara, solo el último cuadro, reconexión automática
        ▼
 CameraPipeline (un hilo por cámara)
   ├─ Detector ........ YOLO | HOG | movimiento  → detecciones con categoría
   ├─ Tracker ......... IDs estables, historial, velocidad y predicción
   ├─ TamperDetector .. integridad de la cámara (tapada, movida, etc.)
   ├─ RuleEngine ...... reglas de la MODALIDAD ACTIVA (ModeManager + horario)
   │        │ RuleHit
   │        ▼
   │   AlertManager ── anti-repetición ─► SQLite · capturas · ClipRecorder (clips)
   │        ├─► EventBroadcaster ─► panel web (Server-Sent Events)
   │        └─► NotifierHub ─► sonido · Telegram · webhook · correo (un hilo por canal)
   └─ Annotator ─► FrameHub ─► panel web (video en vivo)

 FastAPI (panel + API REST) · CLI (python -m vigia ...)
```

```
vigia/
├── cli.py            comandos (iniciar, demo, publicas, validar, ...)
├── config.py         lectura y validación del YAML; guardado de zonas
├── modes.py          modalidades, horario y selección manual persistente
├── system.py         arma todo y hace el mantenimiento (retención, estado)
├── pipeline.py       procesamiento de cada cámara
├── capture.py        captura de video con reconexión
├── annotate.py       dibujo de zonas, líneas, objetos y estado
├── hub.py            último cuadro por cámara y eventos en vivo
├── publicas.py       cliente de cámaras públicas de Caltrans
├── demo.py           video sintético de demostración
├── vision/           detectores, seguimiento y sabotaje
├── rules/            las 11 reglas y el motor
├── alerts/           modelo, SQLite, clips, notificaciones y gestor
└── web/              servidor FastAPI y panel (HTML/CSS/JS, sin compilación)
tests/                98 pruebas automáticas
```

Datos generados, en `datos/`: `vigia.db` (historial), `capturas/AAAA-MM-DD/`,
`clips/AAAA-MM-DD/`, `vigia.log` y `estado.json`.

## 11. API REST

La documentación interactiva está en `/api/docs`.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/estado` | Estado del sistema y de cada cámara |
| GET | `/api/modalidades` | Modalidades, reglas y horario |
| POST | `/api/modalidad` | `{"modalidad": "ausente"}` o `"auto"` |
| GET | `/api/camaras` | Cámaras con sus zonas y líneas |
| GET | `/api/camaras/{id}/cuadro?despues=N` | Último cuadro anotado (JPEG) |
| GET | `/api/camaras/{id}/captura` | Último cuadro sin anotaciones |
| GET | `/api/camaras/{id}/video` | Video MJPEG, por ejemplo para abrir en VLC |
| PUT | `/api/camaras/{id}/geometria` | Guarda zonas y líneas |
| GET | `/api/alertas` | Historial (`limite`, `antes_de`, `camara`, `severidad_min`, `pendientes`) |
| GET | `/api/alertas/{id}` | Una alerta |
| POST | `/api/alertas/{id}/reconocer` | Marca una alerta como reconocida |
| POST | `/api/alertas/reconocer-todas` | Marca todas |
| POST | `/api/prueba` | Envía una alerta de prueba |
| GET | `/api/eventos` | Eventos en vivo (SSE): `alerta`, `estado`, `modalidad`… |

Ejemplo para armar el sistema desde otro programa o un botón físico:

```bash
curl -X POST http://localhost:8080/api/modalidad -H "Content-Type: application/json" -d "{\"modalidad\": \"ausente\"}"
```

## 12. Comandos

```bash
python -m vigia iniciar [-c config.yaml] [--abrir] [--host 0.0.0.0] [--puerto 8080] [--modalidad ausente]
```

| Comando | Para qué sirve |
|---|---|
| `iniciar` | Inicia la vigilancia y el panel. Es el comando por defecto. |
| `demo` | Demostración con video sintético, sin cámaras |
| `publicas` | Busca cámaras públicas en vivo (`--distrito`, `--buscar`, `--cantidad`, `--crear`, `--iniciar`) |
| `camaras` | Lista las webcams conectadas |
| `validar` | Revisa la configuración y muestra qué reglas aplican en cada cámara |
| `probar-alerta` | Envía una alerta de prueba por los canales configurados |
| `modelo` | Descarga y verifica el modelo YOLO |

## 13. Rendimiento

- **Medición real** (Intel Core i5-11300H, 8 hilos, sin GPU; 3 cámaras de
  Caltrans en vivo, una de ellas 1080p con unos 20 vehículos; YOLO `yolo11n` a
  4 detecciones por segundo y unos 7 fps por cámara): **24 % de la CPU del equipo
  y unos 760 MB de RAM**.
- Con la configuración de ejemplo actual (webcam + autopista con `yolo11n` + el
  cruce con `yolo11s` a 3 detecciones por segundo), de día y con mucho tráfico:
  **41 % de la CPU** (3,3 núcleos) y unos 550 MB. El modelo `yolo11s` del cruce
  es la mayor parte de la diferencia: si el equipo va justo, vuelve esa cámara a
  `yolo11n` o baja su `fps_deteccion` a 2.
- Esa cifra incluye una optimización hecha durante la prueba. Al principio, los
  hilos de PyTorch de cada cámara se quedaban girando entre inferencias y el
  consumo era del **64 %**. Ahora duermen (`KMP_BLOCKTIME=1`,
  `OMP_WAIT_POLICY=PASSIVE`) y cada inferencia usa pocos hilos
  (`deteccion.hilos`), sin perder cuadros por segundo.
- Con GPU NVIDIA usa `dispositivo: cuda:0`.
- Para ahorrar CPU, baja `fps_deteccion` (3 o 4 alcanza para personas
  caminando) y deja `fps_reposo: 1`: si la escena está quieta, se analiza 1 vez
  por segundo.
- Los clips se guardan en H.264 (MP4) cuando el equipo lo permite; en Windows
  se usa Media Foundation. Si no, se usa WebM o MP4v; el panel ofrece
  descargarlos si el navegador no puede reproducirlos.
- La detección de movimiento (`tipo: movimiento`) funciona hasta en una
  Raspberry Pi, pero no distingue personas de sombras o animales.

## 14. Limitaciones, privacidad y uso responsable

- Es una **ayuda** para la vigilancia, no un sistema certificado. Habrá falsos
  positivos y falsos negativos: ajusta `confianza`, los tiempos y las zonas a
  cada cámara, y revisa las alertas.
- **De noche**, el modelo `yolo11n` reconoce los autos con poca confianza. En
  la autopista I-80 de Caltrans, con el umbral de 0.40 se marcaba menos de
  un auto por cuadro, y además los autos rápidos no llegaban a confirmarse.
  Con `confianza: 0.25` en las cámaras públicas y más distancia de seguimiento
  para vehículos, se marcan unos 3 o 4 por cuadro. Al bajar el umbral, el
  modelo también confunde con autos un cartel verde y una baliza del borde:
  ver en la sección 7 cómo se evita.

  En la autopista, el modelo más grande `yolo11s` no mejoró de noche y era el
  doble de lento; en cambio, de día en el cruce de Long Beach detecta 3 veces
  más camiones. De noche se pierden autos que solo muestran los faros.
- Los autos muy cercanos cruzan la imagen en un segundo o dos: a 4 detecciones
  por segundo, algunos se marcan tarde o no se marcan, y el recuadro puede
  quedar un poco corrido respecto del auto (se dibuja donde debería estar según
  su velocidad).
- Con poca luz, cámaras lejanas o de baja resolución, YOLO detecta menos. El
  sabotaje solo detecta **cambios**: si la cámara ya estaba tapada al
  arrancar, esa vista pasa a ser la referencia. En la prueba real esto pasó con
  una cámara de Caltrans que ya estaba borrosa.
- La regla de altercado es heurística (experimental). Las armas de fuego
  requieren un modelo específico.
- **Privacidad:** cumple la normativa de videovigilancia y protección de
  datos de tu país. Avisa con carteles que hay cámaras, no enfoques espacios
  privados de terceros y conserva las grabaciones solo el tiempo necesario
  (`retencion_dias`). Vigía no identifica personas: solo detecta categorías y
  comportamientos.

## 15. Pruebas automáticas

```bash
pip install pytest httpx
```

```bash
python -m pytest
```

Son 98 pruebas y cubren: geometría, seguimiento (incluidos los casos reales
del cartel y la baliza), cada regla, sabotaje, horarios y modalidades,
configuración (incluido el guardado de zonas sin perder comentarios),
historial, anti-repetición, clips y límite de espacio, reconexión de una
transmisión congelada, cliente de cámaras públicas, API web y una prueba de
punta a punta que pasa el video de la demo por todo el sistema y verifica que
aparezcan las alertas esperadas.

Además corren solas en GitHub en cada push a `main` y en cada pull request,
en Windows y Linux con Python 3.10 y 3.12. El flujo está en
`.github/workflows/pruebas.yml`; el indicador del principio de este README
muestra si la última ejecución pasó. Ahí se instalan las dependencias sin YOLO
ni PyTorch, porque las pruebas no los usan.

## 16. Solución de problemas

| Problema | Qué hacer |
|---|---|
| "No se pudo abrir la fuente" | Revisa la URL con VLC. En RTSP prueba `rtsp_tcp: true` y la contraseña. En webcams usa `python -m vigia camaras`. |
| La webcam la usa otro programa | Cierra Zoom, Teams, la app de Cámara, etc. |
| Muchas falsas alarmas | Sube `deteccion.confianza` o `seguimiento.confirmaciones`, sube `segundos_min` / `segundos`, o achica las zonas. |
| No detecta a nadie | Baja `confianza` (0.3), sube `tamano_imagen` (960) y verifica que el log diga `detector 'yolo11n'` (o el modelo que uses). |
| No marca vehículos que se ven claramente | Si están quietos, baja `confianza_quietos` para esa cámara (bloque `seguimiento`). Si el modelo no los ve (ángulos raros, camiones desde arriba), prueba `modelo: modelos/yolo11s.pt` en su bloque `deteccion`. |
| Un cartel u objeto fijo aparece como vehículo | Suele desaparecer solo en unos 30 s (se aprende como decorado). Si no, dibuja encima una zona llamada `ignorar_cartel`. |
| Cámara pública "sin señal" | Las transmisiones de Caltrans se congelan cada tanto: Vigía reconecta solo en unos 15 s. Si la cámara dejó de transmitir, vuelve a generar la lista con `python -m vigia publicas --crear config.publico.yaml --forzar`. |
| El panel no carga desde el celular | Usa `--host 0.0.0.0`, abre `http://IP-DEL-EQUIPO:8080`, permite el puerto en el firewall y define `usuario`/`clave`. |
| Mensaje "tag ... is not supported" de OpenCV | Es un aviso inofensivo al escribir clips WebM. |
