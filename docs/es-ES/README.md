# mcp-server-diwall-browser

[English](../../README.md) | [Français](../fr-FR/README.md) | [Deutsch](../de-DE/README.md) | **Español**

![El ojo de Diwall MCP ilumina la web: todo aquello sobre lo que el modelo puede actuar lleva un marco numerado, y el campo de la contraseña queda en negro](../images/scene-diwall-mcp.png)

Un servidor MCP que da a un modelo de lenguaje los ojos y las manos de un operador en el propio navegador del
operador, mediante el protocolo Chrome DevTools: el modelo ve la misma captura de pantalla y el mismo árbol de
accesibilidad que el operador, y actúa en la misma sesión autenticada. Necesita un navegador de la familia
Chromium que el operador inicia con un perfil dedicado y un puerto de depuración enlazado a la interfaz de
bucle local, y un directorio cifrado (gocryptfs) para las credenciales, las capturas y las descargas.

## Antes del primer uso

El servidor no funciona sin un directorio cifrado: el perfil del navegador al que se conecta guarda sus sesiones
iniciadas, y no deben quedar en claro en el disco. No existe ningún modo sin cifrado. Tras la instalación, el primer
comando, que usted ejecuta en una terminal, es `mcp-server-diwall-browser-setup`: lee su máquina y termina con el
siguiente comando que debe teclear.

- `mcp-server-diwall-browser-setup create --temporary` crea un volumen en memoria, para un ensayo. Cerrarlo, apagar la
  máquina o terminar su sesión lo destruye, y con él las sesiones de los sitios que usted abrió en el navegador, el
  diario de auditoría y las capturas.
- `mcp-server-diwall-browser-setup create` crea un volumen duradero.
- `mcp-server-diwall-browser-setup open`, `browser` y `close` abren el volumen, inician el navegador sobre su perfil y
  detienen ambos.

Usted teclea la contraseña, cuando `gocryptfs` se la pide; el asistente no la lee nunca. Usted inicia sesión en sus
sitios, en el navegador que él inicia. Tras añadir el servidor a su cliente MCP, reinicie el cliente: una sesión ya
abierta no lo ve. Los pasos, los rechazos y el camino manual están en la sección «Primer arranque» de
[docs/es-ES/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Los sitios que lee el modelo

La lista de sitios del perfil del operador limita lo que el modelo lee y en lo que actúa, nunca adónde navega. La clave `origin_policy` elige lo
que hace el servidor con un origen que la lista no contiene:

- `strict` (el valor por defecto, también cuando falta la clave): el origen se rechaza, y solo se devuelve el origen.
- `discovery`: el servidor le pregunta en una ventana, una vez por origen: `Refuser` (la respuesta por defecto), `Cette séance` o
  `Ajouter à la liste`, que escribe ese origen exacto en su perfil.
- `open`: todo sitio público se lee y se actúa en él, y su contenido va al proveedor del modelo; las direcciones y los nombres locales siguen en
  la lista. Es la política más libre y la más arriesgada, y la elección es suya.

Los pagos, las eliminaciones irreversibles y los cambios de secreto conservan su confirmación, y una credencial se escribe solo en el origen
exacto al que está ligada, en las tres políticas. El detalle está en la sección 5 de [docs/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Instalar

En Linux, el servidor se entrega en paquetes nativos. Descargue el de su sistema desde la página
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) del repositorio o desde la [guía de instalación del sitio web](https://mcp-diwall.davalan.fr/es/guias/instalar/), y luego instálelo desde la
carpeta que lo contiene:

| Sistema | Comando |
|---|---|
| Debian 13, Ubuntu 24.04 y 26.04 | `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb` |
| Fedora 44 | `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm` |
| openSUSE Leap 16.0 | `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm` |
| Arch Linux | `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst` |

La instalación, el arranque del servidor por una cuenta ordinaria y la desinstalación están validados en
contenedores en estos sistemas. Un contenedor no muestra ni la ventana de confirmación, ni el montaje del
directorio cifrado, ni un navegador. Debian 12 y Ubuntu 22.04 no son compatibles: su Python es anterior a
3.12. La instalación necesita una conexión de red: el paquete crea un entorno virtual de Python e instala en
él las dependencias, en las versiones que el proyecto probó. La página de manual es
`man mcp-server-diwall-browser`. Para instalar desde un clon del repositorio, siga
[docs/es-ES/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

Una máquina ARM de 64 bits toma el mismo `.deb`, porque el paquete no depende de la arquitectura: se probó una Raspberry Pi 4 con Debian 13 (véase «Lo que está probado y lo que no»). El navegador allí es el Chromium de la distribución.

## Conectarlo a un cliente MCP

El servidor habla MCP por la entrada y la salida estándar. Con un paquete instalado, el comando es
`mcp-server-diwall-browser` y el perfil de operador se lee en su sitio habitual:

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

Desde un clon del repositorio, con Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE="$HOME/.config/mcp-server-diwall-browser/profile.json" \
  -- "$HOME/mcp-server-diwall-browser/.venv/bin/python" -m mcp_server_diwall_browser
```

Para otro cliente MCP, el bloque genérico `mcpServers`. El cliente no expande `~`: este comando
imprime el bloque con su directorio personal en las rutas, listo para pegar en su configuración:

```
cat <<EOF
{
  "mcpServers": {
    "diwall-browser": {
      "command": "$HOME/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "$HOME/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
EOF
```

Adapte `~/mcp-server-diwall-browser` al lugar donde clonó el repositorio, y la ruta del perfil al
lugar donde escribió su perfil de operador. La variable puede omitirse cuando el perfil de operador está en su
sitio habitual, `~/.config/mcp-server-diwall-browser/profile.json`. La forma del comando `claude mcp add` se
comprobó con `claude mcp add --help` y después la ejecutó el operador en Debian: `claude mcp list` mostró el
servidor conectado. Las pruebas inician el servidor exactamente como estos dos bloques:
`python -m mcp_server_diwall_browser` con `MCP_DIWALL_BROWSER_PROFILE` definida.

Todo lo anterior (instalar, iniciar el navegador, abrir el directorio cifrado, escribir el perfil de operador)
está en [docs/es-ES/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md). Cómo funciona y por qué está en
[docs/es-ES/ARCHITECTURE.md](ARCHITECTURE.md).

## Lo que ve el modelo

![La página de demostración tal como la recibe el modelo](../images/observe-demo.png)

La imagen de arriba la produjo `scripts/smoke_test.py` a partir de la página de demostración local
(`docs/demo/index.html`). Las marcas numeradas las dibuja el servidor: una acción designa un elemento por su
marca. El campo de contraseña, que la propia página rellenó, queda oculto bajo un rectángulo macizo, y su valor
se retiene del texto que recibe el modelo (`redacted_fields` vale 1).

## Lo que está probado y lo que no

Probado:

- La batería completa, en Debian 13 con Python 3.12.11 y las dependencias de `requirements.lock`: sin navegador
  pasan 2100 pruebas, 1 se omite (una prueba que solo se ejecuta como root) y las 589 que necesitan un navegador
  quedan deseleccionadas; con Google Chrome 156.0.8078.47 (sin interfaz, perfil temporal vacío) pasan 2689
  pruebas y 1 se omite, sin fallo esperado.
- Pruebas de descarga: dos fallaban al azar, 7 y 8 veces en 30 ejecuciones de cada una por separado, porque el
  servidor cerraba el ajuste de descarga antes de que el navegador hubiera elegido qué hacer con la descarga.
  Tras el cambio fallaron 3 y 1 veces en 130 ejecuciones de cada una por separado; las 3 eran una lectura, en la
  propia prueba, de un archivo que el navegador renombra, que ahora es un único ayudante que ignora el cambio de
  nombre. Desde entonces cada una de las dos por separado ha fallado 0 veces en 200 ejecuciones, dos veces
  seguidas. Una tercera prueba, que cerraba el ajuste enseguida y esperaba que una descarga tardía fuera
  rechazada, falló 4 veces en 200 por separado: el navegador real puede elegir antes de que el servidor cierre
  el ajuste, así que la prueba se sustituye por pruebas sobre un navegador simulado. Las mediciones y lo que
  queda están en `docs/JOURNAL.md`.
- El operador repitió una revisión anterior en Debian (Python 3.13.5) con Chrome 154.0.8037.97: sin navegador
  pasaron 1450 y 1 se omitió (una prueba que solo se ejecuta como root); con él pasaron 119 y 6 fallaron como se
  esperaba, las seis del zoom de página, cerradas desde entonces; las dos pruebas de descarga por separado, 0
  fallos en 40 ejecuciones cada una. Después el operador repitió `ff8c433` (la revisión anterior a las pruebas de
  clic) en el mismo navegador: sin navegador pasaron 1546, 1 se omitió y 188 quedaron deseleccionadas; los tres
  archivos de zoom y escala, 125 pasaron; las 188 pruebas de navegador pasaron todas; las dos pruebas de descarga
  y las dos que sustituyen a la inestable, cada una por separado, 0 fallos en 40 ejecuciones. Las pruebas de clic
  no se repitieron en él.
- `scripts/smoke_test.py` devuelve 0 frente a Google Chrome 155.0.8059.84 y 3 cuando no se encuentra ningún navegador.
- Una Raspberry Pi 4 Model B de 8 GB, Raspberry Pi OS sobre Debian 13 (aarch64), Python 3.13.5 y Chromium 155.0.8059.79, el 22 de octubre de 2026: el `.deb` final instalado por `apt`; el primer arranque del asistente de instalación de principio a fin con el `gocryptfs` real (volúmenes temporal y duradero, navegador, cierre); `scripts/smoke_test.py` que devuelve 0; el servidor manejado por un cliente MCP real sobre un Chromium visible, con ventana y no sin interfaz, en una pantalla X11 virtual, con la ventana de confirmación nativa de `zenity` abierta y dejada expirar; luego el paquete retirado y purgado, sin ningún archivo restante. La batería completa dio allí 2694 pruebas superadas y 1 omitida en 15 minutos (cinco son mediciones de ese equipo que el repositorio no contiene). En esa red, la primera página que cargó un Chromium recién iniciado tardó de 15 a 26 segundos, lo que hizo fallar tres pruebas en una primera pasada; con el navegador aislado de la red, todas pasan (guía, sección 12).
- Las marcas y las máscaras a una escala de pantalla de 1, 1,5 y 2, en un Chromium 141 sin interfaz: el cuadro de
  cada marca visible está a menos de dos píxeles del cuadro que da el propio navegador, cada píxel del campo de
  contraseña de la página de demostración está bajo su máscara, y la máscara no se extiende más de dos píxeles más
  allá (`tests/test_scale_integration.py`). Antes de la corrección, una máscara no cubría su campo a una escala
  superior a 1. No se midió una pantalla real a 1,5.
- El zoom de página, al 100, 125, 150 y 200 % y a escalas de pantalla de 1 y 1,5, en un Chromium 141 sin interfaz
  cuyo perfil vacío guarda el zoom como valor por defecto: la imagen que recibe el modelo es la vista completa, el
  campo de contraseña de la página de demostración está bajo su máscara, y también el de una página desplazada
  para que quede cerca del borde inferior o en medio de la vista; la máscara no se extiende más de dos píxeles más
  allá del campo, y en los 16 emplazamientos medidos ningún borde de máscara quedó dentro de su campo
  (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Antes de la corrección, al 125,
  150 y 200 %, el 0 % del campo quedaba cubierto y `capture` leía `ok`. No se publica ninguna imagen (`capture`
  lee `unavailable`, el árbol se sigue sirviendo) cuando el navegador no da un zoom utilizable, cuando el zoom
  cambia durante la captura o cuando la imagen difiere en más de dos píxeles del tamaño de la vista en cualquiera
  de los dos ejes (`tests/test_perception_zoom.py`, que no necesita navegador). En Chrome 154 el operador midió el
  tamaño de la imagen recortada tal como la lee el navegador (la vista a cada zoom, un píxel menos de alto al
  125 %); las pruebas de zoom se repitieron en él más tarde (abajo).
- Un clic del servidor da en su objetivo con zooms de página del 100, 125, 150 y 200 % y escalas de pantalla de 1
  y 1,5, en un Chromium 141 sin interfaz cuyo perfil vacío guarda el zoom como valor por defecto. El clic es el
  del propio servidor (`Actions.click`, con su compuerta y sus límites); la página de prueba cuenta lo que recibe
  cada uno de sus botones y una sesión aparte lee los recuentos. En cada una de las ocho combinaciones, un botón de
  120 por 32 píxeles CSS, un botón de 16 por 16 entre dos señuelos a 4 píxeles, un botón dos vistas y media más
  abajo y el botón grande después de desplazar la página para ponerlo en medio de la vista recibieron exactamente
  un clic, ningún señuelo recibió ninguno y ninguno cayó fuera de un botón. En una página desplazada en ambas
  direcciones el clic da en el botón puesto en medio de la vista, y un botón cubierto por una cabecera fija a la
  vista se rechaza (`element_obscured`) sin hacer clic en nada (`tests/test_click_integration.py`, 264 pruebas;
  `tests/test_click_targets_page.py`, 51, y `tests/test_click_lookup_point.py`, 20, no necesitan navegador). La
  misma comprobación rechaza un clic enviado al punto del cuadro multiplicado por el zoom, dividido por él o
  multiplicado por la escala de pantalla (64 de 64 clics así no alcanzaron ningún botón), y un servidor que
  pregunta al navegador por el punto de la vista. Antes de la corrección, 20 de los 32 intentos en la primera
  página fallaban, los que necesitaban un desplazamiento, y un botón cubierto por la cabecera se aceptaba y se
  hacía clic en la cabecera, en las ocho combinaciones: el navegador responde a la búsqueda de nodo para un punto
  del documento, y el clic le daba un punto de la vista. Las mediciones están en `docs/JOURNAL.md`.
- Un clic en una página que se mueve cuando el puntero se mueve por primera vez (un menú que se abre al pasar por
  encima, una cabecera que se desliza). La página de prueba desplaza cada objetivo 60 píxeles CSS hacia abajo en
  el primer movimiento del ratón y coloca un botón intruso donde estaba el objetivo. En cada una de las ocho
  combinaciones de zoom de página y escala de pantalla, para cada uno de tres botones (uno grande, uno de 16 por 16
  entre dos señuelos, uno dos vistas y media más abajo), un clic del servidor da una vez en el objetivo o se
  rechaza por cubierto (`element_obscured`) sin hacer clic en nada, y en los 24 intentos se rechaza siempre
  (`tests/test_click_shifting_integration.py`, 80 pruebas; `tests/test_click_shifting_page.py`, 30, y
  `tests/test_click_moving_page.py`, 10, no necesitan navegador). Antes del cambio, los mismos 24 intentos no
  devolvían ningún error y el clic daba en el intruso. Tras un rechazo, un clic sobre una observación nueva da en
  su objetivo. El servidor vuelve ahora a preguntar al navegador qué hay bajo el punto después del movimiento y
  antes de la pulsación, cada vez, de modo que también se rechaza un objetivo cubierto donde está por un botón que
  aparece en el primer movimiento (`tests/test_click_covering_integration.py`, 3 pruebas, en Chrome
  154.0.8037.97). Los 24 intentos anteriores se midieron en Chromium 141.0.7390.37. Las mediciones, y las de una
  página que se desplaza sin parar, están en `docs/JOURNAL.md`.
- Una página densa con un banner de consentimiento fijo sobre su parte baja (643 cosas donde hacer clic): sus tres
  botones están marcados y listados primero en el árbol, los elementos que quedan debajo dicen `covered`, y un clic
  en «Rechazar» por su marca quita el banner (`tests/test_ranking_integration.py`).
- El asistente de instalación, `mcp-server-diwall-browser-setup`: 50 pruebas sobre una máquina simulada (tablas de
  montaje, gestores de paquetes, sin `/dev/fuse`, sin terminal, un volumen LUKS); después, el 20 de octubre de 2026, con el
  gocryptfs 2.5.1 real en un pseudoterminal, sobre volúmenes desechables: `create --temporary`, el servidor leyendo
  `mounted`, `browser` (Google Chrome 155.0.8059.84 sin pantalla, conectado, `profile_encrypted: true`), `close` (ningún
  volumen queda en memoria, ningún archivo del perfil del navegador en el disco), después `create`, `close` y `open` de un
  volumen duradero; y en un contenedor Debian 13 limpio, donde `check` dio `sudo apt install gocryptfs fuse3`, que funcionó.
- El comando que traspasa un archivo de credenciales antiguo (véase la guía del operador): sobre archivos de la
  batería de pruebas en un volumen simulado, con un rechazo probado para cada salida, y sin ningún valor en nada de
  lo que imprime. No se probó en un volumen gocryptfs real.
- Un volumen gocryptfs 2.5.1 real, desechable, hecho para la comprobación (no el del operador), montado en Debian:
  `status` leyó `mounted` y `profile_encrypted: true`, y el servidor escribió en él su diario y creó `downloads/`.
  Se conectó Opera 136 (Chromium 152), iniciado con su perfil en ese volumen y un puerto de depuración en la
  interfaz de bucle local; el servidor abrió una página de un sitio público de compras, rellenó su campo de
  búsqueda, pulsó Intro y observó los resultados. El texto escrito no aparece en el diario.
- La política de orígenes (`strict`, `discovery`, `open`): `tests/test_origin_policy.py` y `tests/test_origin_policy_server.py` sobre un navegador simulado, y `tests/test_origin_policy_integration.py`, 12 pruebas del servidor iniciado por su punto de entrada contra Google Chrome 155.0.8059.84, con programas de ventana simulados: un portal que remite a un segundo origen, una redirección a un origen desconocido, las tres respuestas de la ventana (el perfil releído tras `Ajouter à la liste`), nombres locales rechazados con `open`, un pago que sigue pidiendo su ventana, y la política cambiada en el perfil sin reiniciar. Las ventanas de los `kdialog` y `zenity` reales se mostraron en una pantalla virtual y se respondieron con pulsaciones de teclas (Intro y Escape dan el rechazo); una persona las respondió el 22 de octubre de 2026 (la entrada siguiente).
- La ventana de confirmación, mostrada en el equipo del operador y respondida por una persona el 22 de octubre de 2026
  (revisión `0f4f613`), con el volumen gocryptfs propio del operador montado y una página de pago local, en Opera
  138.0.6044.24, una vez con `kdialog` 25.04.0 y otra con `zenity` 4.1.90 (el servidor solo encontraba esa herramienta):
  con cada una, un rechazo devolvió `confirmation_denied` y la página no recibió nada; una aceptación hizo el clic y la
  página recibió una petición; sin respuesta, la petición caducó a los 90,4 segundos (`confirmation_expired`) y la página
  no recibió nada. El diario contiene una línea `requested` y una línea con el resultado para cada una de las seis
  peticiones.
- Claude Code, iniciado desde un terminal de escritorio: `status` a través de Claude Code leyó
  `confirmation_channel: native`, de modo que la pantalla llegó al servidor.
- El proceso del servidor por la entrada y la salida estándar: no escribe nada más que el protocolo en su salida
  estándar, no mantiene ningún socket a la escucha y se ejecuta con un límite de archivo core de cero.

No probado:

- Un equipo ARM de 64 bits distinto de esa Raspberry Pi 4 (una Raspberry Pi 5, otra distribución), una sesión de escritorio Wayland y una persona que responda a la ventana de confirmación en ese equipo.
- El asistente de instalación en una máquina con área de intercambio (su informe se probó sobre una lista simulada), y
  su comando `browser` con Brave, Chromium u Opera.

- Brave, y las compilaciones de Google Chrome distintas de la que ejecutó el operador.
- La confirmación pedida a través de un cliente MCP de escritorio (elicitation): probada solo con el cliente del
  SDK de MCP.
- Chrome 154 con su perfil guardado dentro del directorio cifrado propio del operador.
- El zoom de página hecho con la tecla Ctrl + de un navegador que no es sin interfaz. El protocolo no tiene ningún
  comando de zoom, así que las pruebas escriben el zoom por defecto de un perfil vacío; que el navegador guarde las
  dos clases del mismo modo no está confirmado. Tampoco se midió una pantalla cuya escala sea realmente 1,5; las
  pruebas de clic tienen los mismos dos límites.
- Las pruebas de clic en Chrome 154. La corrección lee una propiedad de la búsqueda de nodo del navegador medida en
  Chromium 141 (responde para un punto del documento, no de la vista): si difiere en otra versión, las pruebas de
  un clic en una página desplazada fallan allí. No se midió un clic en un elemento dentro de un marco de una página
  desplazada.
- Un control que actúa con la pulsación y no con el clic, puesto bajo el puntero por un script en el instante de la
  pulsación: la pulsación se comprueba después de hacerse, así que los manejadores de la página para la pulsación se
  han ejecutado. La guarda de una tecla y de un texto es un script de la página: una página que la espera la rodea, y
  no ve los eventos de un marco (Google Chrome 156.0.8078.47, `docs/OPERATOR_GUIDE.md`, sección 7).

Límites conocidos:

- El directorio cifrado protege tanto como su contraseña: quien tiene la contraseña lee su contenido, y una contraseña débil o reutilizada protege poco.
- Las salvaguardas son las que se midieron en la fecha de esta versión, con las pruebas y mediciones que nombra este documento. Un modelo más reciente que los probados puede encontrar una forma de eludirlas que ninguna prueba ha visto.
- Quien no quiera estas salvaguardas encontrará otros servidores MCP que controlan un navegador sin ellas.
- Mientras el servidor está conectado al navegador, las descargas se rechazan en todas las pestañas, incluidas las
  del propio operador, salvo en la breve ventana en torno a un clic o una tecla Intro del servidor. Para descargar
  un archivo usted mismo, detenga el servidor o el cliente MCP; las descargas vuelven a funcionar en cuanto termina la
  conexión del servidor.
- Se rechaza una descarga que una página inicia más de medio segundo después de que termine una carga.
- Aceptar un cuadro de diálogo `confirm` o `prompt` pide una confirmación al operador en una ventana, diga lo que diga su
  mensaje.
  Aceptar un cuadro de diálogo cuyo mensaje pide un pago, un borrado o un cambio de secreto se rechaza siempre: no
  se puede tomar ninguna captura mientras hay un diálogo abierto. El operador responde a ese diálogo.
- Un secreto que un sitio escribe en la ruta de una dirección se muestra tal cual en los resultados; solo se redactan el
  valor de un parámetro de consulta cuyo nombre contiene una palabra como `token` y el fragmento.

- La visibilidad es aproximada: el servidor no calcula el contraste ni el recorte por `overflow`, así que un texto
  que una persona no puede leer puede llegar igualmente al modelo.
- Una observación contiene como máximo 150 marcas y 400 líneas de árbol (`marks_truncated`, `tree_truncated`). Más
  allá, el servidor conserva primero lo que está en una capa por encima de la página (un diálogo, un nodo modal, un
  nodo fijo o pegajoso pintado sobre contenido a la vista, como un banner de cookies), listado bajo una línea
  `overlay` en el árbol, después lo que está a la vista y despejado, después lo que está a la vista bajo una capa
  (`covered: true`, y un clic sobre ello se rechaza), después el resto. Un banner que no solapa nada a la vista no
  es una capa y aún puede cortarse.
- El contenido de los marcos nunca se lee; los marcos se enmascaran en la imagen.
- Lo que el proveedor del modelo conserva de lo que recibe no está confirmado por este proyecto.

## Créditos

mcp-server-diwall-browser se construye en una colaboración asimétrica entre humano y LLM. Los roles están escritos
tal como se desempeñaron realmente; la lista completa está en [CONTRIBUTORS.md](../../CONTRIBUTORS.md).

- **Ronan Davalan**: arquitecto y árbitro. Visión, requisitos de seguridad, decisiones, validación y pruebas.
- **Claude Code (Anthropic; Opus 5.5, Sonnet 5.5 y Haiku 5.5)**: ingeniero de sistemas y desarrollador principal. Implementación del servidor, pruebas, documentación, marco de trabajo, texto y mantenimiento del sitio del proyecto, la escena que ilustra la primera versión y su breve clip, renderizados en Blender en el equipo de trabajo, controlado mediante LibreModular-MCP, e informe de campo del primer arranque desde el paquete Debian.
- **Claude Cloud (Anthropic)**: escribió parte del código en ramas, a partir de instrucciones, revisado antes de cada fusión.
- **Muse Spark 1.3 Contributor (Meta, mediante OpenCode)**: primer uso del servidor por un modelo distinto de Claude, en una instancia local de una aplicación web, cuyo informe de campo orientó las correcciones posteriores; la hoja de estilo y la maquetación del sitio del proyecto, elegidas entre varios ensayos, con el texto y el comportamiento de las páginas intactos; dos revisiones del sitio, con propuestas de redacción en cuatro idiomas dejadas como borradores y defectos encontrados, que se corrigieron.
- **DeepSeek V4 Pro (DeepSeek, mediante OpenCode)**: auditoría de seguridad independiente de la etiqueta `v0.0.1`, sobre una sesión virgen; cuatro de sus siete hallazgos se confirmaron en la clasificación y se corrigieron.
- **GLM 5.3 (Zhipu, mediante OpenCode)**: auditoría de seguridad independiente de la misma etiqueta, sobre una sesión virgen, con reproducciones en un navegador sin pantalla; tres de sus siete hallazgos se confirmaron en la clasificación y se corrigieron.
- **DeepSeek V4.1 Flash 1017 (DeepSeek, mediante OpenCode)**: el emblema en 3D «la D de cristal», dibujado en Blender en el equipo de trabajo, en dos variantes de pupila (una pupila redonda y un puntero); es el ojo de la escena que ilustra la primera versión.
- **Gemini (Google)**: asesor de arquitectura y sintetizador; dio al proyecto su nombre, **Diwall**, «dique» en bretón.
- **gemini-3.5-flash (Google, mediante OpenCode)**: probó la conexión mediante un agente dedicado: las herramientas solo llegan a ese agente, y su shell pregunta antes de ejecutar.
- **Space Bunny Free (opencode)**: limpieza y poda del marco de trabajo del proyecto al comienzo del proyecto.

Licencia: MIT, véase [LICENSE](../../LICENSE).
