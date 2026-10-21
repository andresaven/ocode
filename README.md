# ocode

ocode es una aplicación con interfaz de terminal (TUI) para gestionar instancias
de OpenCode en contenedores Linux, organizadas por proyecto y working copy.
Está orientada a Windows y mantiene la configuración de cada instancia fuera
del código del proyecto.

Cada instancia permite utilizar **OpenCode TUI y Web simultáneamente**, con
el mismo workspace, sesiones y datos. Los contenedores son persistentes:
cerrar ocode, la TUI de OpenCode o el navegador no elimina la instancia.

## Requisitos

- Windows con Python **3.11 o superior**, disponible como `python` en el PATH,
  y `pip` operativo.
- Un motor capaz de ejecutar contenedores Linux, instalado y en ejecución.
  ocode permite elegir Docker, Docker vía WSL, Podman, detección automática
  o un comando personalizado.
- Acceso a Internet para instalar las dependencias Python, obtener la imagen
  de OpenCode y construir los runtimes Java.
- Una working copy existente y accesible desde el motor de contenedores.
- Un navegador para utilizar la interfaz Web.

El instalador instala las dependencias Python, incluida `textual>=0.89`.
Los runtimes se basan en **OpenCode 2.0.24** e incluyen Java, Maven, Node.js,
npm y Bash; estas herramientas no necesitan estar instaladas en Windows
para usarlas dentro de la instancia.

## Instalación

Descomprime la distribución dentro de `C:\entorno_desarrollo`, de modo que
el instalador quede en `C:\entorno_desarrollo\ocode\install\install.cmd`.
Después, ejecuta:

```powershell
cd C:\entorno_desarrollo\ocode\install
.\install.cmd
```

El instalador valida los archivos obligatorios de la distribución, copia la
aplicación a `app\`, instala el paquete Python y añade `app\bin` al PATH
del usuario. Conserva la configuración activa si ya existe.

Abre una **terminal nueva** y ejecuta:

```powershell
ocode
```

La instalación queda organizada así:

```text
C:\entorno_desarrollo\ocode\
├── README.md
├── install\           Instaladores y contenido de la distribución
│   ├── install.cmd
│   ├── install.py
│   ├── uninstall.cmd
│   ├── uninstall.py
│   └── payload\
├── app\               Aplicación instalada y plantillas
├── update.cmd         Lanzador de actualización
├── config\            Configuración global activa
├── projects\          Configuración de las instancias
├── runtimes\          Dockerfiles generados para Java
└── logs\
```

Las carpetas de ejecución y `update.cmd` se crean durante la instalación;
no es necesario que existan en la distribución antes de instalar.

El lanzador `ocode` fija `OCODE_HOME` a la carpeta de instalación. Al ejecutar
directamente `python -m ocode`, puedes definir esa variable para utilizar
otra ubicación; sin ella se usa `C:\entorno_desarrollo\ocode`.

## Primer inicio

En el primer inicio aparece **Motor de contenedores**:

1. Selecciona cómo invocar el motor: Docker, Docker vía WSL, Podman,
   detección automática o comando personalizado.
2. Para un comando personalizado, indica, por ejemplo, `wsl -d Ubuntu docker`.
3. Pulsa **Guardar y probar**.

La selección queda guardada aunque la prueba falle. Puedes revisarla después
desde **Motor** en el menú principal. ocode invoca los ejecutables del motor;
no modifica aliases ni perfiles de PowerShell.

## Añadir un proyecto o working copy

En el menú principal, pulsa **Añadir**:

1. Introduce la ruta de la working copy o selecciónala con **Navegar...**.
2. Revisa el nombre del proyecto y la rama / working copy.
3. Elige Java **8, 11, 17 o 21**.
4. Selecciona el modo predeterminado y el puerto Web.
5. Pulsa **Guardar** para abrir la gestión de la instancia.

ocode no clona repositorios ni crea ramas: utiliza una carpeta existente.
El nombre de proyecto puede inferirse de la ruta; el selector de carpetas
también rellena la rama / working copy. Para una ruta como:

```text
C:\repositorios\UGV\UGV-MUERTEMER
```

se propone el proyecto `UGV` y la working copy `UGV-MUERTEMER`. En layouts
SVN con `branches` o `tags`, se toma como proyecto el directorio superior
a esas carpetas. Puedes ajustar estos nombres antes de guardar.

La working copy se monta con lectura y escritura en `/workspace`.
**Los cambios realizados por OpenCode en esa ruta afectan a los archivos
originales del host**, salvo las rutas enmascaradas por la protección.

Cada instancia publica su puerto Web. Utiliza puertos distintos para
instancias que deban funcionar al mismo tiempo. El puerto predeterminado
es `4096` y la dirección de escucha en el host es `127.0.0.1`.

## Gestionar instancias

Selecciona una fila del menú principal y pulsa **Gestionar instancia** o Enter.
La pantalla muestra proyecto, rama, working copy, Java, modo predeterminado,
puerto y número de reglas de protección.

El estado se consulta al abrir o volver a la pantalla. **Actualizar estado**
permite consultarlo de nuevo; si el motor no responde se muestra
«no disponible».

### Uso

- **Abrir TUI:** inicia o reutiliza el contenedor y conecta una TUI de OpenCode
  al servidor de la instancia. Al cerrar OpenCode se vuelve a ocode.
- **Abrir Web:** inicia o reutiliza el mismo contenedor y abre el navegador
  con acceso autenticado. El diálogo muestra la URL sin datos de acceso.
- **Detener:** detiene el contenedor sin eliminar configuración, sesiones
  ni datos persistentes.

Web y TUI comparten el servidor y pueden utilizarse simultáneamente.
Cerrar una interfaz no detiene la otra. Salir de ocode tampoco detiene
las instancias.

Las instancias creadas a partir de ocode 0.10.0 toman de `config\config.json`
los valores `web_show_agent` (por defecto `true`) y `web_color_scheme`
(por defecto `"dark"`; admite `"light"`). Son valores iniciales de la
**interfaz Web**, no ajustes de la TUI ni del agente predeterminado. Al abrir
la Web desde ocode se aplican únicamente si el navegador aún no tiene esas
preferencias para esa dirección y puerto. Cada instancia conserva sus valores
iniciales aunque cambie la configuración global; los cambios realizados desde
el navegador no se sobrescriben. Las instancias anteriores no se modifican.
La selección de tema no está incluida.

### Ajustes y recreación

Desde **Ajustes** puedes cambiar la working copy, Java, modo predeterminado,
puerto y activación de instrucciones o configuración específicas del proyecto.
**Abrir configuración** abre la carpeta correspondiente en Windows.

Para guardar y aplicar los ajustes o las reglas de protección, pulsa
**Guardar y recrear contenedor** en **Ajustes** o **Protección de archivos**.
La operación requiere confirmación y:

- aplica la imagen, puertos, configuración y montajes actuales;
- conserva la working copy, la configuración y los mismos volúmenes de datos
  y estado de OpenCode;
- interrumpe las tareas activas;
- descarta los archivos guardados únicamente en la capa interna del contenedor,
  fuera de montajes y volúmenes persistentes;
- deja el contenedor sustituto en ejecución.

Detener y arrancar un contenedor no cambia su imagen, puertos ni montajes.
Para cambiar Java, prepara antes la imagen correspondiente desde **Java**:
la recreación exige que la imagen esté disponible.

Si falla la recreación, los ajustes pueden haber quedado guardados sin
aplicarse. Los volúmenes se conservan y, si se creó un contenedor, también
se conserva para diagnóstico. Revisa el error antes de volver a intentarlo.

### Eliminar una instancia

**Eliminar instancia** requiere confirmación y que la instancia esté detenida.
Elimina su carpeta de configuración en ocode, el contenedor y los volúmenes
persistentes de datos y estado, incluidas sus sesiones.

**No modifica la working copy. Es una operación irreversible:** conserva antes
las instrucciones, configuración y datos que necesites. No la utilices para
aplicar ajustes; para eso está **Guardar y recrear contenedor**.

## Runtimes Java

La pantalla **Java** lista los perfiles y sus imágenes:

| Perfil | Imagen |
| --- | --- |
| Java 8 | `ocode-runtime:2.0.24-maven3.9.9-java8` |
| Java 11 | `ocode-runtime:2.0.24-maven3.9.9-java11` |
| Java 17 | `ocode-runtime:2.0.24-maven3.9.9-java17` |
| Java 21 | `ocode-runtime:2.0.24-maven3.9.9-java21` |

Selecciona un perfil y pulsa **Construir seleccionado** para preparar su imagen.
Todos incluyen Maven y npm. Los Dockerfiles se generan en
`runtimes\<perfil>\` a partir de `ghcr.io/anomalyco/opencode:2.0.24`.
Maven se fija en la versión **3.9.9** del repositorio `community` de Alpine
3.21; Java y los demás paquetes proceden de la distribución de la imagen
base. La etiqueta nueva evita reutilizar imágenes anteriores con otra versión
de Maven. Para aplicar el cambio a una instancia existente, prepara su
runtime desde **Java / Maven** y usa **Guardar y recrear contenedor** en sus
Ajustes; detenerla y arrancarla no cambia la imagen.

Al abrir una instancia que todavía no tiene contenedor, ocode construye
automáticamente el runtime si su imagen no está disponible. La primera
apertura puede tardar mientras se descarga y construye la imagen.

En **Ajustes** de cada instancia puedes indicar la ruta absoluta a tu carpeta
Maven `.m2\repository` (opcional). ocode monta **solo esa carpeta** en
`/root/.m2/repository`, en lectura. Maven ve las dependencias que descargues
posteriormente en el equipo sin copiar la caché; usa `mvn -o` para compilar
sin consultar repositorios remotos. La ruta debe existir y terminar en
`repository`. Guardar este ajuste requiere confirmar la recreación del
contenedor; detenerlo y arrancarlo no cambia los montajes.

La pantalla **Java / Maven** permite guardar una ruta por defecto para
**nuevas instancias**. Al añadir una working copy se muestra esa ruta y puedes
cambiarla o dejarla vacía para no montar ninguna. Cada instancia conserva su
propio valor: modificar el predeterminado no afecta a las existentes.

OpenCode también puede leer los artefactos y metadatos de la carpeta montada.
Comprueba por tu cuenta que no haya datos de acceso incrustados en ellos; ocode
no monta el resto de `.m2` ni la configuración de Maven. El repositorio es de
solo lectura para el contenedor: si Maven necesita escribir metadatos, alguna
compilación puede fallar. Las dependencias no descargadas previamente en el
equipo tampoco estarán disponibles en modo offline.

En entornos donde la working copy vive en una unidad de Windows montada por
WSL, Maven puede fallar al copiar recursos por no poder aplicar permisos POSIX
en el destino. Para esos casos, **Ajustes** admite una lista de **directorios
de build en volumen Linux** (opcional): rutas relativas a la working copy,
separadas por `;`, por ejemplo `wseganadero/target`. ocode monta un volumen
nombrado en cada una, así que el compilador escribe en un sistema de archivos
Linux. Cada instancia tiene sus propios volúmenes.

Para configurarlo:

1. Al añadir la working copy (**Añadir working copy**) o en **Ajustes** de la
   instancia, rellena **Directorios de build en volumen Linux** con las rutas
   relativas separadas por `;`, por ejemplo `wseganadero/target`.
2. Pulsa **Guardar y recrear contenedor**: el montaje solo se aplica al recrear.
3. Compila dentro del contenedor, por ejemplo `mvn -o -DskipTests package`.
4. Revisa **Almacenamiento** para confirmar que el volumen del directorio está
   montado en lectura y escritura.

Consecuencias a tener en cuenta:

- El `target` (o el directorio elegido) de Windows **no se actualiza** desde el
  contenedor: quedan dos artefactos independientes, el del IDE y el de ocode.
- El volumen es persistente: se conserva al recrear la instancia y se elimina al
  eliminar la instancia. Aparece en la vista de **Almacenamiento**.
- El directorio es **escribible dentro del contenedor** y persiste; no puede
  solaparse con las reglas de protección, pero no sustituye a esas reglas.
- Guardar este ajuste requiere **Guardar y recrear contenedor**; detener y
  arrancar no cambia los montajes.
- Solo cubre las salidas escritas bajo los directorios indicados. Otras salidas
  que el proyecto escriba fuera de ellos siguen dependiendo del montaje.
- El directorio debe ser de **salida** (como `target`). Un volumen sobre una
  carpeta de código o de recursos la ocultaría dentro del contenedor, aunque no
  la borra del equipo.

## Configuración e instrucciones

La configuración activa vive fuera de la working copy:

| Archivo | Función |
| --- | --- |
| `config\config.json` | Ajustes globales de ocode y motor de contenedores. |
| `config\AGENTS.md` | Instrucciones globales para OpenCode. |
| `config\opencode.jsonc` | Configuración global de OpenCode. |
| `config\skills\` | Skills globales de OpenCode (`<id>\SKILL.md`). |
| `config\secrets\opencode-go.env` | Clave de API de OpenCode Go (texto plano). |
| `config\secrets\openai.env` | Clave de API de OpenAI (texto plano). |
| `projects\<Proyecto-Rama>\project.json` | Identidad, working copy y ajustes de la instancia. |
| `projects\<Proyecto-Rama>\AGENTS.md` | Instrucciones específicas del proyecto. |
| `projects\<Proyecto-Rama>\opencode.jsonc` | Configuración específica de OpenCode. |
| `projects\<Proyecto-Rama>\skills\` | Skills específicas de esa instancia (`<id>\SKILL.md`). |
| `projects\<Proyecto-Rama>\sensitive-paths.txt` | Reglas de protección mediante montajes. |

`projects\` contiene configuración, **no una copia del repositorio**.
Los nombres de carpeta se adaptan a Windows y reciben un sufijo si hay colisiones.

Las instrucciones específicas están activadas por defecto. La configuración
específica `opencode.jsonc` está desactivada por defecto; ambas opciones se
controlan desde **Ajustes**.

El `AGENTS.md` global se monta en solo lectura; el específico se monta con
escritura en `/etc/ocode/project-AGENTS.md` cuando está activado. ocode combina
las instrucciones en el `AGENTS.md` global del contenedor y sincroniza sus
cambios mientras la instancia está encendida. Las instrucciones globales indican que
prevalecen ante contradicciones, aunque OpenCode combina instrucciones y no
resuelve conflictos automáticamente. Una petición general para crear o editar
el AGENTS del proyecto se aplica al archivo externo de la instancia; solo se
crea o edita `/workspace/AGENTS.md` si se pide expresamente ese archivo del repo.

Las skills globales se descubren desde la ubicación global de OpenCode. ocode
monta las skills de cada proyecto fuera de `/workspace` y las registra desde la
configuración global, por lo que no dependen de activar el JSONC específico.
Las skills generales son de solo lectura; las del proyecto y el AGENTS
específico permiten escritura desde OpenCode.

La configuración global de OpenCode también se monta en solo lectura.
La específica, cuando está activada, se monta en `/opencode.jsonc`, fuera
del workspace. OpenCode la descubre como configuración de un directorio
ancestro y la combina con la global. La configuración propia del repositorio
sigue visible y tiene precedencia sobre esa configuración externa.

ocode no crea ni monta sus archivos de configuración dentro de `/workspace`.
Los archivos `AGENTS.md` y `opencode.jsonc` propios del repositorio no se ocultan.

Para aplicar cambios en `opencode.jsonc` o en la activación de los montajes,
utiliza **Guardar y recrear contenedor**. Los cambios de contenido en los
`AGENTS.md` montados se sincronizan sin recrear. Los cambios de skills quedan
visibles en las carpetas montadas; si la sesión no las detecta, inicia una nueva.
Instancias existentes requieren recreación para incorporar los nuevos montajes.

**Config global** abre la carpeta de configuración. Las plantillas de
`app\defaults\` sirven para inicializar archivos; edita los archivos activos
de `config\` y `projects\`, no las plantillas.

### Proveedores

El menú **Proveedores** lista los proveedores disponibles y permite guardar la
clave de API de cada uno sin ejecutar `/connect` dentro de cada contenedor.
Al seleccionar un proveedor se abre un diálogo para introducir, guardar o
borrar su clave. Cada clave se almacena en su propio fichero de
`config\secrets\` (texto plano) y se inyecta en los contenedores nuevos
mediante `--env-file`; no se escribe en `config.json`, en `opencode.jsonc` ni
en `auth.json`, y no aparece en la línea de comandos ni en **Comando Docker**.

| Proveedor | Fichero | Variable | Prefijo de modelo |
| --- | --- | --- | --- |
| OpenCode Go | `config\secrets\opencode-go.env` | `OPENCODE_API_KEY` | `opencode-go/<modelo>` |
| OpenAI | `config\secrets\openai.env` | `OPENAI_API_KEY` | `openai/<modelo>` |

La clave de OpenCode Go cubre también OpenCode Zen (`opencode/<modelo>`); el
prefijo del modelo decide cuál se factura.

Las claves son globales: todas las instancias comparten las mismas y no se
admiten claves distintas por instancia. Los contenedores existentes conservan
la configuración con la que se crearon; tras guardar, cambiar o borrar una
clave, cada instancia afectada muestra un aviso y necesita **Guardar y recrear
contenedor** desde sus Ajustes. ocode nunca recrea contenedores automáticamente.

Las claves se guardan en texto plano: cualquier persona con acceso al perfil de
Windows del usuario puede leer los ficheros. El agente que se ejecuta dentro del
contenedor puede leer su propio entorno (`env`) y `docker inspect` muestra las
variables; `sensitive-paths.txt` no protege frente a eso. Usa claves dedicadas
y revocables, y no las reutilices para otros servicios. ocode nunca muestra el
valor completo: solo el estado (configurada o no) y, como máximo, los últimos
cuatro caracteres.

## Protección de archivos

Cada instancia guarda sus reglas en
`projects\<Proyecto-Rama>\sensitive-paths.txt`. Puedes editarlas directamente
o desde **Protección de archivos**.

```text
# Una regla por línea; se permiten comentarios y líneas vacías.
src/main/resources/certs
/k8s/
src/main/resources/configuration/prod
```

Las reglas se normalizan como `**/...`, admiten `/` y `\`, y rechazan unidades
de disco y segmentos `..`. Se interpretan como sufijos de ruta contra:

- la raíz de la working copy;
- módulos Maven declarados en `pom.xml`;
- directorios hijos directos.

No se realiza un barrido recursivo general del repositorio. El prefijo `**/`
no significa que se busque a cualquier profundidad.

Las rutas existentes se enmascaran dentro del contenedor, sin modificar
los originales: los directorios aparecen como directorios vacíos de solo
lectura y los archivos se montan sobre `/dev/null`, también en solo lectura.

Las instancias se inicializan con reglas para configuraciones de entorno,
certificados, archivos de aplicación, recursos de base de datos y `k8s`.
Puedes adaptar esas reglas a cada proyecto. Un fichero vacío es válido y
una regla sin coincidencias no bloquea el arranque. Un fichero de protección
ausente, ilegible o inválido produce un error.

Las ediciones manuales se respetan: guardar otros ajustes no sobrescribe
las reglas ni reintroduce las eliminadas. Tras editar el archivo fuera de
ocode, reabre la pantalla para cargarlo.

En **Protección de archivos**, **Añadir** y **Eliminar seleccionado** modifican
un borrador. El informe muestra las coincidencias de ese borrador en la
working copy, **no los montajes reales del contenedor existente**.
Pulsa **Guardar y recrear contenedor** para guardar y aplicar los cambios.

Los permisos `deny` de `opencode.jsonc` son una barrera independiente:
quitar una regla de `sensitive-paths.txt` no elimina esos permisos. La
protección por montajes tampoco sustituye la revisión de permisos y reglas
de uso de OpenCode.

## Acceso Web

La instancia ejecuta `opencode serve` detrás de un proxy local. El proxy
dirige las peticiones HTTP y WebSocket al workspace `/workspace` y gestiona
la autenticación efímera. **Abrir Web** abre el navegador con un acceso de
un solo uso; los datos sensibles no aparecen en la URL mostrada ni en la
vista de comandos.

Por defecto el puerto se publica únicamente en `127.0.0.1`. No expongas el
servicio a otras máquinas sin revisar previamente la configuración de red
y seguridad.

Si el backend termina inesperadamente, el proxy intenta reiniciarlo con
una espera progresiva. Cuando se corta una respuesta, un flujo de eventos
o un WebSocket, se liberan las conexiones para que el navegador detecte el
fallo y pueda reconectar.

**El proxy no reenvía automáticamente prompts interrumpidos**, porque el
backend podría haberlos aceptado antes del corte. Comprueba la sesión antes
de repetir una petición.

## Atajos y almacenamiento

Estos atajos pertenecen a la TUI de **ocode**, no a la TUI de OpenCode:

| Atajo | Acción |
| --- | --- |
| `Ctrl+P` | Abrir la paleta de atajos. |
| `Ctrl+←` | Volver a la pantalla anterior. |
| `Ctrl+→` | Recuperar la pantalla siguiente. |
| `q` | Salir de ocode sin detener las instancias. |

El botón **Opciones**, arriba a la derecha, muestra las acciones del contexto:
en la pantalla principal, los archivos y Skills generales; en la gestión de
una instancia, los archivos, Skills y reglas del proyecto, además del
almacenamiento. Los archivos se abren con la aplicación predeterminada de
Windows y las carpetas Skills en el Explorador.

**Ver almacenamiento del proyecto** muestra los montajes reales del
contenedor, sus permisos, los volúmenes persistentes y las rutas de
configuración en el host. Distingue la configuración actual de lo que está
montado realmente y permite actualizar la consulta o abrir la working copy
en el Explorador de Windows. **Explorar contenedor** permite recorrer nombres
de directorios de una instancia en ejecución; es de solo lectura y no inicia
instancias detenidas.

Los volúmenes de datos y estado viven en el almacenamiento del motor;
en Docker Desktop/WSL no son carpetas normales de Windows. Los archivos
de la capa interna del contenedor no tienen una correspondencia individual
editable en el host. Esta vista no lee el contenido de los archivos ni
modifica los volúmenes. El explorador interno solo lista directorios; no abre
ni lee archivos.

## CLI y diagnóstico

Además del lanzador `ocode`, puedes utilizar:

```powershell
python -m ocode
python -m ocode doctor
python -m ocode runtime build java17
```

Sin subcomando se abre la TUI. `doctor` comprueba el motor, la existencia
de los archivos globales, las imágenes runtime y las working copies.
`runtime build` construye el perfil indicado: `java8`, `java11`, `java17`
o `java21`.

**La CLI inicializa la configuración antes de ejecutar cualquier subcomando,
incluido `doctor`; no es una operación sin efectos sobre archivos locales.**

En la TUI, **Diagnóstico** ofrece la misma comprobación general. Desde la
gestión de una instancia, **Logs** muestra los logs del contenedor y
**Comando Docker** permite ver el comando de creación, arranque o conexión
que corresponda, con los datos sensibles redactados.

Si falla un arranque, el contenedor se conserva para poder consultar el
diagnóstico. Comprueba el motor, la imagen Java, la disponibilidad del puerto
y la ruta de la working copy antes de eliminar datos.

## Actualizar

ocode comprueba al iniciar si hay una versión nueva publicada en GitHub y, sin
bloquear la TUI, muestra un aviso con la versión disponible y sus notas. Desde
el aviso puedes descargar e instalar la actualización o **Omitir esta versión**.
La descarga usa el ZIP de código fuente del tag de la release; si aún no hay una
release publicada, se tiene en cuenta el tag estable más alto.

Para actualizar manualmente, cierra ocode, descomprime la distribución sobre la
misma carpeta y ejecuta:

```powershell
cd C:\entorno_desarrollo\ocode
.\update.cmd
```

También puedes elegir **Buscar actualizaciones** en Opciones para
comprobarlo en cualquier momento, **Actualizar ocode** para abrir `update.cmd`
tras cerrar ocode. La comprobación automática se controla mediante el ajuste
`check_updates` en `config\config.json`.

La actualización descarga el paquete, lo extrae en la carpeta de instalación y
reutiliza `install\install.cmd` y `install\payload\`. Conserva la configuración
activa, proyectos, contenedores y volúmenes persistentes. Las instancias siguen
funcionando y no se recrean automáticamente.

Si necesitas aplicar una imagen o unos montajes distintos, prepara el
runtime y utiliza **Guardar y recrear contenedor** en la instancia.

## Desinstalar

Para eliminar solo la aplicación:

```powershell
cd C:\entorno_desarrollo\ocode\install
.\uninstall.cmd
```

Para eliminar también las carpetas locales `config`, `projects`, `runtimes`
y `logs`:

```powershell
.\uninstall.cmd --remove-local-data
```

El desinstalador retira la aplicación y su entrada en el PATH del usuario,
pero conserva los instaladores. **No detiene ni elimina contenedores,
imágenes o volúmenes del motor**, incluso con `--remove-local-data`.

Si quieres eliminar las instancias y sus volúmenes, hazlo desde ocode
antes de desinstalar o borrar su configuración local. Detén cada instancia
y utiliza **Eliminar instancia**, conservando previamente los datos que
necesites.
