% MCP-SERVER-DIWALL-BROWSER(1) mcp-server-diwall-browser | Comandos de usuario
% Ronan Davalan
% 2026-10-18

# NOMBRE

mcp-server-diwall-browser - servidor MCP que da a un modelo de lenguaje los ojos y las manos de un operador en su propio navegador

# SINOPSIS

**mcp-server-diwall-browser**

**mcp-server-diwall-browser-credential-import** **\--source** *ARCHIVO* **\--name** *NOMBRE* [**\--origin** *ORIGEN*...]

**mcp-server-diwall-browser-credential-import** **\--source-dir** *DIRECTORIO*

# DESCRIPCIÓN

**mcp-server-diwall-browser** es un servidor del Model Context Protocol (MCP). Se conecta, mediante
el Chrome DevTools Protocol, a un navegador de la familia Chromium (Google Chrome, Chromium o
Brave) que el operador ha iniciado. Da al modelo de lenguaje que ejecuta el cliente MCP la misma
captura de pantalla y el mismo árbol de accesibilidad que al operador, y el medio de actuar en la
misma sesión ya abierta.

El programa no tiene opciones. Un cliente MCP lo inicia como proceso hijo y le habla por su
entrada y su salida estándar. No abre ningún puerto a la escucha y termina cuando el cliente cierra
su entrada. Solo se conecta al puerto de depuración del navegador, en la interfaz de bucle local.
Nunca inicia ni cierra el navegador, y nunca monta ni desmonta el directorio cifrado.

El navegador debe funcionar con un perfil propio, nunca con el perfil habitual del operador:

    google-chrome --user-data-dir=DIRECTORIO --remote-debugging-port=9222

El servidor rechaza un perfil predeterminado (`profile_dir_is_default`) y un puerto de depuración
accesible más allá del bucle local (`debug_endpoint_not_loopback`). Las credenciales, las capturas,
las descargas y el diario de auditoría se guardan en un directorio cifrado (gocryptfs) que abre el
operador; mientras está cerrado, el servidor no escribe nada.

Instalar el programa no lo configura. Escriba primero el perfil del operador (véase **PERFIL DEL
OPERADOR**) y conecte después el servidor a un cliente MCP; con Claude Code:

    claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser

La instalación, el navegador, el directorio cifrado y cada código de rechazo se describen en la
guía del operador (véase **ARCHIVOS**).

# HERRAMIENTAS

El servidor ofrece catorce herramientas al modelo.

**status**
:   Lo que está configurado, abierto, conectado y permitido, y lo que bloquea. Funciona sin nada configurado.

**list_tabs**, **open_tab**, **close_tab**
:   Las pestañas de este servidor. El servidor solo trabaja en las pestañas que ha abierto, nunca en una pestaña del operador.

**navigate**
:   Cargar una dirección http o https en una de sus pestañas.

**observe**
:   Devolver la imagen con marcas numeradas, el árbol de accesibilidad y la lista de elementos. Las marcas solo valen para esa observación.

**click**, **fill**, **select_option**, **press_key**, **scroll**, **handle_dialog**
:   Actuar sobre un elemento designado por su marca. Un campo sensible solo se rellena desde un archivo de credenciales (**fill** con *credential_ref*), nunca desde un texto.

**wait**
:   Esperar la carga de la página, un texto o un elemento.

**save_evidence**
:   Guardar una captura en el directorio cifrado.

# PERFIL DEL OPERADOR

El servidor vuelve a leer el perfil del operador en cada llamada: un cambio surte efecto sin
reiniciar. Es un archivo JSON que se busca en `$XDG_CONFIG_HOME/mcp-server-diwall-browser/profile.json`,
o en `~/.config/mcp-server-diwall-browser/profile.json` cuando `XDG_CONFIG_HOME` no está definida,
o en la ruta absoluta que indica la variable `MCP_DIWALL_BROWSER_PROFILE`. El archivo tiene modo
600 y su directorio modo 700; de lo contrario el servidor lo rechaza (`profile_invalid`). El
archivo no contiene ningún secreto.

`schema_version`
:   Siempre `1`.

`secrets_dir`
:   El lugar donde está abierto el directorio cifrado (ruta absoluta).

`secrets_crypt_dir`
:   El directorio cifrado en el disco (opcional).

`allowed_origins`
:   Los sitios que el servidor lee y en los que actúa: orígenes, o comodines de subdominio como `https://*.example.invalid`. De cualquier otra página solo se devuelve el origen.

`confirm_each_change`
:   Orígenes de `allowed_origins` en los que cada cambio pide una confirmación.

`local_model_origins`, `declarations`
:   Declaraciones del operador, que **status** muestra y que no sirven para nada más.

`browser`
:   `family` (`chrome`, `chromium` o `brave`), `profile_dir` (el `--user-data-dir` del perfil propio) y `debug_port` (de 1024 a 65535, 9222 por defecto).

`limits`
:   `min_action_delay_ms` (250 por defecto), `max_actions_per_session` (200) y `max_pages_per_session` (50).

Una clave que configurase las acciones bloqueadas (pago, eliminación) se rechaza: solo el código
decide qué está bloqueado. La guía del operador da un ejemplo completo.

# CONFIRMACIÓN

Antes de un pago, una eliminación irreversible o un cambio de secreto, y antes de cada cambio en un
origen de `confirm_each_change`, el servidor abre, mediante **kdialog** o **zenity**, una ventana
titulada «Confirmation du serveur MCP» con los botones «Autoriser» y «Refuser». La ventana espera
90 segundos y después cuenta como falta de respuesta; una respuesta cubre una sola acción. Necesita
una pantalla en el entorno del servidor (`DISPLAY` o `WAYLAND_DISPLAY`); **status** indica el canal
en `confirmation_channel`.

# IMPORTACIÓN DE CREDENCIALES

**mcp-server-diwall-browser-credential-import** convierte un archivo de credenciales de otro
formato, guardado en el directorio cifrado abierto, en un archivo que el servidor lee, dentro de su
propio proceso y sin mostrar ningún valor. Ejecútelo usted mismo, en su shell, con el directorio
cifrado abierto.

**\--source** *ARCHIVO*
:   Ruta absoluta del archivo antiguo, dentro del directorio cifrado. Obligatoria con **\--name**, salvo que se indique **\--source-dir**.

**\--name** *NOMBRE*
:   Nombre del archivo nuevo, que pasa a ser `NOMBRE.json` en el directorio `credentials/`.

**\--origin** *ORIGEN*
:   Un origen completo (`https://host`, con puerto si no es el predeterminado). Puede repetirse. Sin ella, cada host del archivo antiguo pasa a ser `https://host`.

**\--source-dir** *DIRECTORIO*
:   Ruta absoluta de un directorio de archivos antiguos; cada archivo que termina en `.json` se convierte por turno. Excluye las tres opciones anteriores.

El comando escribe una línea JSON por archivo en su salida estándar. Solo se trasladan el nombre de
usuario y la contraseña; un segundo factor no. No sobrescribe nada y no elimina nada.

# ENTORNO

`MCP_DIWALL_BROWSER_PROFILE`
:   Ruta absoluta del perfil del operador, en lugar del lugar habitual.

`XDG_CONFIG_HOME`
:   Base del lugar habitual del perfil del operador.

`DISPLAY`, `WAYLAND_DISPLAY`
:   La pantalla en la que se abre la ventana de confirmación.

# PAQUETES

El servidor se entrega en paquetes nativos, que se instalan desde la carpeta que contiene el archivo descargado:

Debian 13, Ubuntu 24.04 y 26.04
:   `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb`

Fedora 44
:   `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm`

openSUSE Leap 16.0
:   `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm`

Arch Linux
:   `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst`

La instalación, el arranque del servidor por una cuenta ordinaria y la desinstalación están validados en contenedores en estos sistemas. Un contenedor no muestra ni la ventana de confirmación, ni el montaje del directorio cifrado, ni un navegador. Debian 12 y Ubuntu 22.04 no son compatibles: su Python es anterior a 3.12.

# ARCHIVOS

`/usr/bin/mcp-server-diwall-browser`, `/usr/bin/mcp-server-diwall-browser-credential-import`
:   Los dos comandos.

`/usr/lib/mcp-server-diwall-browser`
:   El programa y su entorno virtual de Python, que el paquete crea en la instalación y para lo cual necesita una conexión de red.

`/usr/share/doc/mcp-server-diwall-browser/es/OPERATOR_GUIDE.md`
:   La guía del operador: instalación, navegador, directorio cifrado, perfil, credenciales y códigos de rechazo.

`/usr/share/doc/mcp-server-diwall-browser/OPERATOR_GUIDE.md`
:   La misma guía en inglés, el idioma de la fuente.

`~/.config/mcp-server-diwall-browser/profile.json`
:   El perfil del operador.

*secrets_dir*`/mcp-server-diwall-browser/`
:   Lo que el servidor escribe, solo en el directorio cifrado abierto: `credentials/`, `browser-profile/`, `journal/operations.jsonl`, `evidence/`, `downloads/` y `run/tabs.json`.

# CÓDIGO DE SALIDA

**mcp-server-diwall-browser** termina con 0 cuando el cliente MCP cierra su entrada. El rechazo de
una herramienta es un resultado con un código que se devuelve al cliente, no un código de salida.

**mcp-server-diwall-browser-credential-import** termina con 0 cuando se traslada cada archivo y con
1 en caso contrario, tras escribir una línea `error: CODE` en su salida de error.

# VÉASE TAMBIÉN

**gocryptfs**(1), **fusermount3**(1), **kdialog**(1), **zenity**(1)

Proyecto: <https://github.com/RonanDavalan/mcp-server-diwall-browser> y
<https://mcp.diwall.davalan.fr>

# AUTOR

Ronan Davalan. Los demás colaboradores figuran en `CONTRIBUTORS.md`.
