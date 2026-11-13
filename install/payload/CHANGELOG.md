# Cambios

## Sin publicar

- Cada instancia guarda su versión de OpenCode en `project.json`
  (`opencode_version`). La versión global de `config.json` es el valor por
  defecto de las instancias nuevas; al actualizar desde una versión anterior se
  detecta la versión npm instalada en el contenedor, aunque esté detenido, y se
  fija en el proyecto.
- Al añadir una instancia se propone la última versión publicada de OpenCode;
  se puede cambiar antes de guardar. El registro se filtra a OpenCode 2.x desde
  la 2.0.24, la primera que usó ocode; la 1.x y las anteriores no se ofrecen.
- **Actualizar OpenCode** está en la barra superior de la pantalla principal:
  abre un diálogo con la lista de versiones y las novedades de la seleccionada.
  Desde ahí se actualiza a la última publicada o a una versión concreta; después
  muestra el plan y comprueba la versión en npm. Si no está disponible, no se
  modifica ninguna instancia.
- La tabla principal muestra la versión de OpenCode de cada instancia junto a la
  de Java. Es una sola versión, la instalada; ocode sincroniza el proyecto con
  ella y, antes de recrear, comprueba que esté disponible en npm.
- La pantalla de la instancia incluye **Actualizar a la última** y **Elegir
  versión…**: ejecutan
  `opencode upgrade <versión> --method npm`, reinician y verifican la versión. No
  reconstruyen imágenes; conservan sesiones y datos; las instancias detenidas se
  arrancan y se vuelven a detener.
- **Runtimes** permite cambiar la versión de reserva (solo para instancias
  nuevas) y **Actualizar todas** abre el mismo diálogo de versiones y novedades;
  se detiene al primer fallo y deja las siguientes sin tocar.
- Al recrear un contenedor se reaplica la versión fijada. Si el registro npm no
  está disponible, el contenedor queda en la versión de la imagen base, la
  interfaz lo avisa y no se pierden datos.
- La lista de versiones se consulta en el registro de OpenCode; también se puede
  escribir una versión a mano si no hay red o no aparece.
- Antes de actualizar, el diálogo de todas las instancias y la confirmación
  muestran las novedades publicadas de la versión elegida y un botón **Abrir
  cambios**; si no hay notas, abre la página de releases.

## 0.12.3

- OpenCode Console (`opencode/<modelo>`) y OpenCode Go (`opencode-go/<modelo>`)
  tienen claves independientes: Console conserva `OPENCODE_API_KEY` y Go pasa a
  `OPENCODE_GO_API_KEY`, con ficheros `opencode-console.env` y
  `opencode-go.env`.
- OpenCode Console puede usarse sin clave en modo gratuito; con clave, el uso se
  atribuye a la cuenta y se habilitan los modelos de pago. Es independiente de
  OpenCode Go.
- Se añade OpenRouter como proveedor (`openrouter/<modelo>`, variable
  `OPENROUTER_API_KEY`, fichero `openrouter.env`), independiente de OpenAI.
- Al actualizar, la clave existente de OpenCode Go se migra a la nueva variable
  y la configuración global recibe `providers.opencode-go.env` aunque ya tuviera
  un bloque `providers`. Se guarda una copia
  `opencode.jsonc.bak-pre-0.12.2` antes de modificarla.
- La huella de la clave incluye el nombre de la variable: los contenedores
  creados antes del cambio se marcan como desactualizados.
- Diagnóstico y Proveedores muestran la separación Console/Go y, si no se pudo
  aplicar, el bloque exacto que hay que añadir a `config\opencode.jsonc`.
- Un proyecto cuyo `opencode.jsonc` declara `providers` puede anular la
  separación Console/Go; Diagnóstico lo avisa.

## 0.12.2

- Con Docker vía WSL, mantener una sesión propia mientras haya instancias de
  ocode activas y liberarla automáticamente al detenerse la última.

## 0.12.1

- Corregir la versión incluida en el paquete para que el actualizador reconozca
  la instalación y no vuelva a ofrecer la misma actualización.
- El actualizador comprueba que la versión anunciada coincida con la del paquete
  antes de instalarlo.

## 0.11.3

- OpenCode puede leer y editar `/tmp/` y sus subdirectorios dentro del contenedor.

## 0.11.2

- La skill `docx` debe cargarse para cualquier operación Word y ahora define
  un flujo Node explícito para extraer contenido y contrastarlo con código.
  La instrucción efectiva del contenedor también exige cargarla antes de actuar.
- La extracción DOCX no se considera imposible solo porque falten los paquetes
  `mammoth` o `docx`; la skill comprueba también herramientas ZIP disponibles.
- Si no hay una vía Node lista, las instrucciones efectivas exigen ofrecer la
  instalación autorizada de una dependencia local antes de abandonar la tarea.

## 0.11.1

- La skill `docx` prioriza Node.js en los contenedores, conserva el estilo del
  documento original y respeta las rutas de trabajo indicadas por el usuario.

## 0.11.0

- Las skills incluidas en `defaults/skills/<id>/SKILL.md` se instalan en las
  skills globales al inicializar ocode, sin sobrescribir las personalizaciones
  existentes del usuario.
- Los defaults de OpenCode usan el formato nativo V2 de permisos; se conserva
  el bloqueo de lectura de archivos `.properties`.
- La skill `grilling` presenta las preguntas de cada ronda mediante un diálogo
  modal en lugar de formularios de texto plano.

- Los runtimes Java incluyen Subversion (`svn`) y Python 3 (`python3`),
  instalados con `apk` al construir la imagen.
- Los permisos predeterminados de OpenCode permiten `svn diff` y `svn diff *`,
  pero bloquean `--diff-cmd` para impedir el uso de un comparador externo.
- Si la imagen del runtime ya existe, hay que reconstruirla desde
  **Java / Maven → Construir seleccionado** para incorporar las herramientas.
  Los contenedores existentes requieren **Guardar y recrear contenedor**.

## 0.12.3

- OpenCode Console (`opencode/<modelo>`) y OpenCode Go (`opencode-go/<modelo>`)
  tienen claves independientes: Console conserva `OPENCODE_API_KEY` y Go pasa a
  `OPENCODE_GO_API_KEY`, con ficheros `opencode-console.env` y
  `opencode-go.env`.
- OpenCode Console puede usarse sin clave en modo gratuito; con clave, el uso se
  atribuye a la cuenta y se habilitan los modelos de pago. Es independiente de
  OpenCode Go.
- Se añade OpenRouter como proveedor (`openrouter/<modelo>`, variable
  `OPENROUTER_API_KEY`, fichero `openrouter.env`), independiente de OpenAI.
- Al actualizar, la clave existente de OpenCode Go se migra a la nueva variable
  y la configuración global recibe `providers.opencode-go.env` aunque ya tuviera
  un bloque `providers`. Se guarda una copia
  `opencode.jsonc.bak-pre-0.12.2` antes de modificarla.
- La huella de la clave incluye el nombre de la variable: los contenedores
  creados antes del cambio se marcan como desactualizados.
- Diagnóstico y Proveedores muestran la separación Console/Go y, si no se pudo
  aplicar, el bloque exacto que hay que añadir a `config\opencode.jsonc`.
- Un proyecto cuyo `opencode.jsonc` declara `providers` puede anular la
  separación Console/Go; Diagnóstico lo avisa.

## 0.12.2

- Con Docker vía WSL, mantener una sesión propia mientras haya instancias de
  ocode activas y liberarla automáticamente al detenerse la última.

## 0.12.1

- Corregir la versión incluida en el paquete para que el actualizador reconozca
  la instalación y no vuelva a ofrecer la misma actualización.
- El actualizador comprueba que la versión anunciada coincida con la del paquete
  antes de instalarlo.

## 0.11.3

- OpenCode puede leer y editar `/tmp/` y sus subdirectorios dentro del contenedor.

## 0.11.2

- La skill `docx` debe cargarse para cualquier operación Word y ahora define
  un flujo Node explícito para extraer contenido y contrastarlo con código.
  La instrucción efectiva del contenedor también exige cargarla antes de actuar.
- La extracción DOCX no se considera imposible solo porque falten los paquetes
  `mammoth` o `docx`; la skill comprueba también herramientas ZIP disponibles.
- Si no hay una vía Node lista, las instrucciones efectivas exigen ofrecer la
  instalación autorizada de una dependencia local antes de abandonar la tarea.

## 0.11.1

- La skill `docx` prioriza Node.js en los contenedores, conserva el estilo del
  documento original y respeta las rutas de trabajo indicadas por el usuario.

## 0.11.0

- Las skills incluidas en `defaults/skills/<id>/SKILL.md` se instalan en las
  skills globales al inicializar ocode, sin sobrescribir las personalizaciones
  existentes del usuario.
- Los defaults de OpenCode usan el formato nativo V2 de permisos; se conserva
  el bloqueo de lectura de archivos `.properties`.
- La skill `grilling` presenta las preguntas de cada ronda mediante un diálogo
  modal en lugar de formularios de texto plano.

## 0.10.0

- Las instancias nuevas heredan las preferencias Web iniciales de ocode:
  Mostrar agente activado y esquema oscuro, configurables globalmente.
- Al abrir la Web se respetan las preferencias existentes del navegador;
  las instancias previas no reciben estos valores iniciales.
- Al crear instancias se consulta el motor de contenedores y se propone el
  primer puerto libre desde el predeterminado; se avisa si pertenece a
  cualquier contenedor, esté o no gestionado por ocode. El campo Puerto no
  admite edición y la instancia no se puede guardar mientras dura la consulta.
- Las instrucciones de las instancias indican que las skills específicas se
  instalan fuera de la working copy; las globales se instalan desde el host
  porque su montaje es de solo lectura.

## 0.9.0

- Varios proveedores de claves, OpenCode Go y OpenAI, cada uno con su fichero
  en `config\secrets` y su etiqueta en el contenedor.
- La pantalla Proveedores lista el estado de cada clave y abre un diálogo para
  guardar o borrar la clave del proveedor seleccionado.
- Las instancias reciben solo las claves configuradas mediante `--env-file` y
  avisan de que deben recrearse cuando una clave cambia o se borra.
- `ocode doctor` muestra el estado de cada proveedor.

## 0.8.0

- La paleta `Ctrl+P` queda reservada para los atajos; las acciones pasan al
  menú Opciones contextual.
- Opciones permite abrir AGENTS.md, opencode.jsonc y Skills generales o del
  proyecto según la pantalla, además de sensitive-paths.txt y almacenamiento.
- La vista de almacenamiento permite abrir la working copy en Windows y
  recorrer directorios del contenedor en modo de solo lectura.
- Skills globales y por instancia fuera de la working copy; las skills del
  proyecto se pueden editar desde OpenCode.
- El AGENTS específico de la instancia se puede editar desde el contenedor y
  queda diferenciado del AGENTS propio del repositorio.

## 0.7.1

- Actualización de versión para validar el sistema de actualización automática.

## 0.7.0

- ocode comprueba al iniciar si hay una versión nueva en GitHub y permite
  actualizar desde la TUI: descarga el paquete, lo extrae en la carpeta de
  instalación, lanza `update.cmd` y cierra ocode.
- La comprobación automática se puede desactivar y cada versión avisada se
  puede omitir. La paleta de atajos añade **Buscar actualizaciones**.

## 0.6.0

- El listado de instancias muestra su estado y puerto.
- Los botones para volver se muestran arriba a la izquierda y destacan más.

## 0.5.1

- Las instancias con puertos distintos pueden coexistir independientemente de
  que sus clientes sean TUI o Web. Los puertos se configuran manualmente.
- Abrir una instancia cuyo puerto está ocupado muestra un error; ya no ofrece
  detener otras instancias para liberar el puerto.
- TUI y Web reutilizan el servidor de los contenedores compartidos. Cerrar un
  cliente no detiene el servidor. Para abrir varias TUI, usa terminales distintas.
- No se cambian puertos ni se recrean contenedores existentes automáticamente.
  Cambiar el puerto de un contenedor existente requiere recreación explícita,
  conservando sus volúmenes. Los contenedores TUI antiguos siguen usando `attach`
  y no adquieren acceso Web automáticamente.
- Pruebas de regresión para concurrencia, colisiones no destructivas, cierre
  del cliente y operaciones limitadas a la instancia seleccionada.
""", "path">
C:\entorno_desarrollo\ocode\install\payload\CHANGELOG.md
