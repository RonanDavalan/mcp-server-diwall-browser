# mcp-server-diwall-browser

[English](../../README.md) | [Français](../fr-FR/README.md) | [Deutsch](../de-DE/README.md) | **Español**

Un servidor MCP que da a un modelo de lenguaje los ojos y las manos de un operador en el propio navegador del
operador, mediante el protocolo Chrome DevTools: el modelo ve la misma captura de pantalla y el mismo árbol de
accesibilidad que el operador, y actúa en la misma sesión autenticada. Necesita un navegador de la familia
Chromium que el operador inicia con un perfil dedicado y un puerto de depuración enlazado a la interfaz de
bucle local, y un directorio cifrado (gocryptfs) para las credenciales, las capturas y las descargas.

## Instalar

En Linux, el servidor se entrega en paquetes nativos. Descarga el de tu sistema desde la página
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) del repositorio o desde la página de descargas del sitio web, y luego instálalo desde la
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
`man mcp-server-diwall-browser`. Para instalar desde un clon del repositorio, sigue
[docs/OPERATOR_GUIDE.md](../OPERATOR_GUIDE.md).

## Conectarlo a un cliente MCP

El servidor habla MCP por la entrada y la salida estándar. Con un paquete instalado, el comando es
`mcp-server-diwall-browser` y el perfil de operador se lee en su sitio habitual:

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

Desde un clon del repositorio, con Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE=~/.config/mcp-server-diwall-browser/profile.json \
  -- ~/mcp-server-diwall-browser/.venv/bin/python -m mcp_server_diwall_browser
```

Para otro cliente MCP, el bloque genérico `mcpServers`:

```json
{
  "mcpServers": {
    "diwall-browser": {
      "command": "~/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "~/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
```

Adapta `~/mcp-server-diwall-browser` al lugar donde clonaste el repositorio, y la ruta del perfil al
lugar donde escribiste tu perfil de operador. La variable puede omitirse cuando el perfil de operador está en su
sitio habitual, `~/.config/mcp-server-diwall-browser/profile.json`. La forma del comando `claude mcp add` se
comprobó con `claude mcp add --help` y después la ejecutó el operador en Debian: `claude mcp list` mostró el
servidor conectado. Las pruebas inician el servidor exactamente como estos dos bloques:
`python -m mcp_server_diwall_browser` con `MCP_DIWALL_BROWSER_PROFILE` definida.

Todo lo anterior (instalar, iniciar el navegador, abrir el directorio cifrado, escribir el perfil de operador)
está en [docs/OPERATOR_GUIDE.md](../OPERATOR_GUIDE.md). Cómo funciona y por qué está en
[docs/ARCHITECTURE.md](../ARCHITECTURE.md).

## Lo que ve el modelo

![La página de demostración tal como la recibe el modelo](../images/observe-demo.png)

La imagen de arriba la produjo `scripts/smoke_test.py` a partir de la página de demostración local
(`docs/demo/index.html`). Las marcas numeradas las dibuja el servidor: una acción designa un elemento por su
marca. El campo de contraseña, que la propia página rellenó, queda oculto bajo un rectángulo macizo, y su valor
se retiene del texto que recibe el modelo (`redacted_fields` vale 1).

## Lo que está probado y lo que no

Probado:

- La batería completa se ejecuta en el entorno de pruebas de este repositorio (Ubuntu 24.04), con Python 3.13.14 y
  las dependencias de `requirements.lock`: sin navegador pasan 1654 pruebas y las 532 que necesitan un navegador
  quedan deseleccionadas (como usuario distinto de root se omite una de las 1654: una prueba que solo se ejecuta
  como root); con Chromium 141.0.7390.37 (sin interfaz, perfil temporal vacío) pasan las 532 pruebas de
  navegador, sin fallo esperado.
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
  fallos en 40 ejecuciones cada una. Después el operador repitió `919ed8c` (la revisión anterior a las pruebas de
  clic) en el mismo navegador: sin navegador pasaron 1546, 1 se omitió y 188 quedaron deseleccionadas; los tres
  archivos de zoom y escala, 125 pasaron; las 188 pruebas de navegador pasaron todas; las dos pruebas de descarga
  y las dos que sustituyen a la inestable, cada una por separado, 0 fallos en 40 ejecuciones. Las pruebas de clic
  no se repitieron en él.
- `scripts/smoke_test.py` devuelve 0 frente a Chromium 141 y 3 cuando no se encuentra ningún navegador.
- Las marcas y las máscaras a una escala de pantalla de 1, 1,5 y 2, en un Chromium 141 sin interfaz: el cuadro de
  cada marca visible está a menos de dos píxeles del cuadro que da el propio navegador, cada píxel del campo de
  contraseña de la página de demostración está bajo su máscara, y la máscara no se extiende más de dos píxeles más
  allá (`tests/test_scale_integration.py`). Antes de la corrección, una máscara no cubría su campo a una escala
  superior a 1. No se midió una pantalla real a 1,5.
- El zoom de página, al 100, 125, 150 y 200 % y a escalas de pantalla de 1 y 1,5, en un Chromium 141 sin interfaz
  cuyo perfil vacío guarda el zoom como valor por defecto: la imagen que recibe el modelo es la vista completa, el
  campo de contraseña de la página de demostración está bajo su máscara, y también el de una página desplazada
  para que quede cerca del borde inferior o en medio de la vista; la máscara no se extiende más de dos píxeles más
  allá del campo, y en los 16 emplazamientos medidos ningún borde de máscara quedó dentro de su campo
  (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Antes de la corrección, al 125,
  150 y 200 %, el 0 % del campo quedaba cubierto y `capture` leía `ok`. No se publica ninguna imagen (`capture`
  lee `unavailable`, el árbol se sigue sirviendo) cuando el navegador no da un zoom utilizable, cuando el zoom
  cambia durante la captura o cuando la imagen difiere en más de dos píxeles del tamaño de la vista en cualquiera
  de los dos ejes (`tests/test_perception_zoom.py`, que no necesita navegador). En Chrome 154 el operador midió el
  tamaño de la imagen recortada tal como la lee el navegador (la vista a cada zoom, un píxel menos de alto al
  125 %); las pruebas de zoom se repitieron en él más tarde (abajo).
- Un clic del servidor da en su objetivo con zooms de página del 100, 125, 150 y 200 % y escalas de pantalla de 1
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
- El comando que traspasa un archivo de credenciales antiguo (véase la guía del operador): sobre archivos de la
  batería de pruebas en un volumen simulado, con un rechazo probado para cada salida, y sin ningún valor en nada de
  lo que imprime. No se probó en un volumen gocryptfs real.
- Un volumen gocryptfs 2.5.1 real, desechable, hecho para la comprobación (no el del operador), montado en Debian:
  `status` leyó `mounted` y `profile_encrypted: true`, y el servidor escribió en él su diario y creó `downloads/`.
  Se conectó Opera 136 (Chromium 152), iniciado con su perfil en ese volumen y un puerto de depuración en la
  interfaz de bucle local; el servidor abrió una página de un sitio público de compras, rellenó su campo de
  búsqueda, pulsó Intro y observó los resultados. El texto escrito no aparece en el diario.
- Claude Code, iniciado desde un terminal de escritorio: `status` a través de Claude Code leyó
  `confirmation_channel: native`, de modo que la pantalla llegó al servidor.
- El proceso del servidor por la entrada y la salida estándar: no escribe nada más que el protocolo en su salida
  estándar, no mantiene ningún socket a la escucha y se ejecuta con un límite de archivo core de cero.

No probado:

- Una ventana real de `kdialog` o `zenity`: las pruebas usan programas simulados en su lugar.
- El volumen gocryptfs propio del operador: las pruebas simulan la tabla de montajes y el directorio, y la
  comprobación anterior usó un volumen desechable.
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
- Una página que se mueve después de la segunda búsqueda de un clic y antes de la pulsación. En una página que se
  desplaza 1000 píxeles CSS por segundo, 6 de 144 clics no dieron ningún error y no cayeron en ninguna parte;
  ninguno cayó en otro botón (Chrome 154.0.8037.97, `docs/JOURNAL.md`).
- Un rechazo en la ventana de confirmación, dado por una persona.

Límites conocidos:

- Mientras el servidor está conectado al navegador, las descargas se rechazan en todas las pestañas, incluidas las
  del propio operador, salvo en la breve ventana en torno a un clic o una tecla Intro del servidor. Para descargar
  un archivo tú mismo, detén el servidor o el cliente MCP; las descargas vuelven a funcionar en cuanto termina la
  conexión del servidor.
- Se rechaza una descarga que una página inicia más de medio segundo después de que termine una carga.
- Aceptar un cuadro de diálogo cuyo mensaje pide un pago, un borrado o un cambio de secreto se rechaza siempre: no
  se puede tomar ninguna captura mientras hay un diálogo abierto. El operador responde a ese diálogo.
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
- **Claude Code (Anthropic)**: ingeniero de sistemas y desarrollador principal. Implementación del servidor, pruebas, documentación y marco de trabajo.
- **Claude Cloud (Anthropic)**: escribió parte del código en ramas, a partir de instrucciones, revisado antes de cada fusión.
- **Gemini (Google)**: asesor de arquitectura y sintetizador; dio al proyecto su nombre, **Diwall**, «dique» en bretón.
- **Space Bunny Free (opencode)**: limpieza y poda del marco de trabajo del proyecto al comienzo del proyecto.
- **Visión local**: Qwen3-VL (Alibaba), ejecutado mediante Ollama, para la localización y la comparación visuales opcionales.

Licencia: MIT, véase [LICENSE](../../LICENSE).
