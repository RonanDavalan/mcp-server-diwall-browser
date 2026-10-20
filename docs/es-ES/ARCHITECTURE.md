# Arquitectura

Por qué el servidor está construido como está. Cómo instalarlo y ejecutarlo está en
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Dos capas

```
el modelo de lenguaje, a través de su cliente MCP    lee, decide, se lo explica al operador
        │  MCP por la entrada y la salida estándar: 14 herramientas
mcp-server-diwall-browser                            ojos y manos: observa, actúa, rechaza
        │  Chrome DevTools Protocol, solo 127.0.0.1
el navegador del operador                            perfil dedicado, sesiones autenticadas

junto al servidor:  directorio cifrado               credenciales de entrada; diario, capturas, descargas de salida
                    ventana de confirmación          kdialog o zenity, respondida por el operador
```

El modelo aporta el criterio; el servidor aporta lo que el modelo no puede tener por sí solo: la misma
imagen y el mismo árbol de accesibilidad que ve el operador, gestos en la propia sesión del operador y los
rechazos que no dependen del criterio del modelo.

## Un paso

1. `open_tab` o `navigate` carga una página en una pestaña que pertenece al servidor.
2. `observe` lee la página: el servidor toma una instantánea, numera con marcas los elementos en los que
   puede actuar, dibuja las marcas y las máscaras en la imagen, y devuelve la imagen, el árbol, los
   elementos y un `snapshot_id`.
3. El modelo nombra una marca de esa instantánea en `click`, `fill`, `select_option` o `press_key`.
4. El servidor cuenta la acción contra los topes, encuentra el nodo del navegador que hay tras la marca,
   comprueba que es el mismo nodo, todavía en la página, en el punto que va a tocar, y que la
   página está en un origen permitido. La compuerta decide si la acción está bloqueada (pago, eliminación,
   cambio de secreto). Una acción bloqueada se anota primero en el diario y se captura, después la ventana de confirmación
   pregunta al operador; la identidad se comprueba de nuevo, se hace el gesto, el servidor espera la
   carga que él inició y vuelve a capturar.
5. El resultado sale por el único punto de salida, que lo censura y separa lo que declara el servidor
   (`server`) de lo que contiene la página (`page_content`). El diario recibe una línea.

## Módulos

| Módulo | Papel |
|---|---|
| `__main__` | punto de entrada: refuerzo, registro en la salida de error estándar mediante la censura, el servidor sobre los flujos estándar |
| `hardening` | archivos core desactivados, umask 077 |
| `origin` | análisis, normalización y comparación de direcciones y orígenes; el comodín de subdominio, un tipo propio |
| `output` | el único punto de salida: censura, envoltorio, `server` y `page_content` mantenidos aparte |
| `sensitive_field` | el predicado que dice que un campo es sensible |
| `mount` | si el directorio cifrado está montado como se espera, y cada ruta dentro de él |
| `credentials` | lectura de los archivos de credenciales, resolución de uno para un origen exacto, y el esquema que otro código puede pedir |
| `credential_import` | un comando del operador, fuera del servidor: convierte un archivo de credenciales antiguo, o un directorio de ellos, al esquema, sin mostrar ningún valor |
| `setup` | un programa del operador, fuera del servidor e importado por ninguno de sus módulos: lee la máquina, crea o abre el volumen gocryptfs, escribe el perfil del operador, inicia el navegador sobre su perfil y cierra ambos; la terminal pasa a gocryptfs, que es el único que lee la contraseña |
| `journal` | el diario de auditoría, una línea JSON por llamada |
| `operator_profile` | lectura del perfil del operador; junto con `confirm`, los únicos módulos del servidor que leen el entorno |
| `profile_watch` | la huella de un cambio de las listas de orígenes del perfil: la comparación, los orígenes vistos por última vez, el aviso |
| `cdp` | el cliente de DevTools, su lista de métodos permitidos y sus scripts constantes |
| `browser` | comprobaciones de la conexión, pestañas que posee el servidor, navegación, diálogos |
| `perception` | instantánea, marcas, árbol, imagen, máscaras, lo que se conserva bajo los topes, espera de un texto o de un elemento |
| `observation_filter` | los parámetros opcionales `region` y `contains` de `observe`: reducen el árbol y los elementos, leyendo solo líneas ya censuradas |
| `gate` | las categorías bloqueadas, su detección, los topes |
| `confirm` | la confirmación: texto de la ventana, ventana nativa o petición del cliente |
| `actions` | clic, relleno, selección de una opción, teclas, desplazamiento, diálogos, por una sola canalización |
| `tab_state` | el archivo de las pestañas que tiene el servidor, para que la siguiente ejecución pueda cerrar las que una ejecución detenida dejó abiertas |
| `evidence` | capturas y descargas escritas en el directorio cifrado |
| `guide` | el texto que lee el modelo cuando se abre la sesión |
| `server` | la fábrica del servidor, sus dependencias, la sesión del navegador, el diario de cada llamada |
| `tools/` | un archivo por herramienta, cada una por el punto de salida |

Cada comprobación de seguridad existe en un solo módulo; un segundo módulo que la necesita llama al primero.
Las pruebas lo comprueban, y también que ninguna herramienta evita el punto de salida.

## Salvaguardas

- Bucle local: el servidor se conecta solo si todos los sockets del puerto de depuración escuchan en el bucle local
  (se lee en `/proc/net` en cada conexión), si el perfil declarado no es el predeterminado del
  navegador, y si el navegador anuncia una dirección de depuración en ese mismo puerto del bucle local.
- Orígenes permitidos: una página fuera de ellos no se lee ni se actúa en ella; solo se devuelve su origen.
  Una entrada es un origen o un comodín de subdominio (`https://*.example.invalid`), escrito por el
  operador; un comodín amplía la lectura y la acción, nunca la escritura. Una credencial se escribe solo en el
  origen exacto que nombra su archivo, y un archivo que enumera un comodín se rechaza.
- Acciones bloqueadas: el pago, la eliminación irreversible y el cambio de secreto se detectan a partir de un vocabulario
  escrito en el código y de campos de tarjeta. Las heurísticas y la `intent_category` del modelo solo pueden
  añadir un bloqueo; nada quita uno, y el perfil del operador no puede configurarlos.
- Confirmación: una acción bloqueada espera la respuesta del operador en una ventana que abre el servidor.
- Fallo cerrado: una tabla de montajes ilegible, una máscara que no se puede dibujar o un diario que no se puede
  escribir acaban en un rechazo, nunca en una comprobación más débil.

## Por qué un cliente de DevTools escrito a mano, con una lista de métodos permitidos

Con una biblioteca de automatización general, lo que el servidor puede enviar al navegador es lo que la biblioteca puede
enviar. Con un cliente propio, es una lista de 27 métodos en un módulo, y una prueba la compara con
cada nombre de método del código. Los scripts se envían solo desde una tabla de textos constantes; los argumentos
de una herramienta viajan en el campo `arguments` del protocolo y nunca se formatean dentro de un script.

## Por qué una marca es el nodo del navegador y no un rango

Una marca está ligada al nodo del navegador para ese elemento en esa instantánea. Un rango
(«el tercer botón») se mueve cuando la página inserta un banner o una línea, y el mismo número designa entonces
otro elemento, posiblemente un botón de pago. Ligado al nodo, un elemento movido o reemplazado se
rechaza (`element_stale`) y el modelo observa de nuevo.

## Por qué las máscaras y las marcas las dibuja el servidor sobre la imagen

Dibujada dentro de la página, una superposición puede ser movida, ocultada o leída por los propios scripts de la página. Dibujada
sobre los píxeles capturados, una máscara cubre lo que el servidor midió, y un valor bajo ella no puede
recuperarse de la imagen; si no se puede dibujar una máscara, no se publica ninguna imagen. Las marcas coinciden con las
cajas de la instantánea contra la que el servidor comprueba cuando el modelo actúa. Cada cuadro está en píxeles CSS:
los píxeles del dispositivo de la instantánea se dividen por la razón de las dos vistas del navegador, de modo que una máscara
queda sobre su campo a cualquier escala de pantalla; sin esa razón la página se rechaza, no se lee a escala 1.

## Por qué se conserva primero una capa por encima de la página

Una observación contiene como máximo 150 marcas y 400 líneas del árbol. Un banner de consentimiento fijo sobre la vista
llega tarde en el documento: en el orden del documento se recortaba, y los elementos bajo él se ofrecían
como si un clic pudiera alcanzarlos. Bajo los topes el servidor conserva, por orden: lo que pertenece a una capa
por encima de la página (un diálogo, un nodo modal, un nodo fijo o pegajoso que pinta sobre contenido a la vista); lo que
está a la vista y despejado; lo que está a la vista bajo una capa (`covered`); el resto. Las líneas del árbol de la capa
van primero bajo una línea *overlay*. Las capas se distinguen solo a partir de la instantánea, por el orden de pintado
y los eventos del puntero, sin una llamada por elemento; un contenedor fijo alrededor de toda la página no es una.

## Por qué la confirmación es una ventana nativa

El cliente MCP actúa por el modelo y puede responder a una petición por sí mismo. Una ventana de `kdialog` o `zenity`,
iniciada por el servidor sin un shell, muestra el estado leído en el navegador en ese momento
y la responde una persona, con el rechazo como botón predeterminado y la falta de respuesta tras 90 segundos
contada como un rechazo. La petición propia del cliente (elicitation) se usa solo cuando el operador declara
que el cliente nunca la responde por sí mismo.

## Decisiones descartadas y qué las reabriría

- Playwright o una biblioteca similar: la lista de lo que envía el servidor sería la de la biblioteca.
  Lo reabriría: un navegador cuyo protocolo de depuración el cliente escrito a mano ya no pueda hablar.
- Una herramienta que ejecute un script: evitaría las marcas, las comprobaciones de identidad, la compuerta y las máscaras, y
  leería todo lo que contiene una página. Lo reabriría: una tarea del operador que las herramientas no puedan hacer, y entonces como
  un script constante nuevo en el cliente, no como un script elegido por el modelo.
- Leer dentro de los marcos: un marco de otro origen es otro sitio, a menudo de pago o
  publicitario; leerlo enviaría al modelo una página fuera de los orígenes permitidos. Los marcos se
  enmascaran. Lo reabriría: un sitio de los orígenes permitidos cuyo contenido necesario viva en un marco
  del mismo origen.
- Un transporte de red: un puerto a la escucha puede ser alcanzado por cualquier proceso local; los flujos estándar atan
  el servidor al cliente que lo inició. Lo reabriría: un cliente MCP que el operador necesite y que
  solo alcance servidores por la red.
- Subir los topes: mantienen acotado lo que recibe el modelo. Lo reabriría: una página cuya única
  capa se recorte con la clasificación actual.
- El formato antiguo de credenciales leído por el servidor: su esquema es estricto para que un archivo no se lea nunca
  como lo que no es. Lo reabriría: un formato que el operador no pueda convertir una vez.
- Una herramienta que convierta credenciales: se entregaría al modelo la ruta de un archivo de secretos, y sus
  respuestas van a su proveedor; el comando se ejecuta en el shell del operador. Lo reabriría: un
  operador sin shell, y una manera de mantener los valores fuera de la sesión del modelo allí.
- Una segunda vía de resolución, para el formato antiguo: dos reglas que ligan una credencial a un origen son
  dos lugares que corregir. Lo reabriría: nada; `resolve` sigue siendo el único lector del único esquema.
- Un contexto de navegador propio del servidor, para que el rechazo de las descargas se quede en las pestañas del servidor:
  sus pestañas no compartirían la sesión autenticada del operador, que es la razón de ser
  del proyecto. Así que mientras el servidor está conectado, también se rechazan las descargas propias del operador. Lo
  reabriría: un navegador que acepte el ajuste de descarga para una sola pestaña, lo cual no está confirmado.
