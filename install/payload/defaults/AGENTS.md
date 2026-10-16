# Reglas globales para agentes (opencode)

Aplican a todos los repos y sesiones; los proyectos solo añaden, nunca relajan.

Entorno monitorizado por EDR: todo proceso hijo de opencode es visible. Ya hubo una alerta
**"Possible Search For Password Files"** por un barrido recursivo con PowerShell. No repetirlo.

Los permisos de `opencode.jsonc` son la barrera real. Si algo está bloqueado o pide
confirmación, **no busques otra vía** (variantes del comando, `webfetch`, copias, scripts):
para y pregunta al usuario.

## Shell: último recurso
- Explorar, buscar y leer con `Glob`, `Grep` y `Read`, nunca con el shell ("Grep" es la
  herramienta, no el comando).
- Shell solo para build/herramientas permitidas. Un comando por llamada: sin `;`, `&&`, `|`,
  bucles ni scripts. Sin ráfagas de comandos de prueba.
- Si un comando es denegado, no lo reintentes ni lo reformules.

## Búsquedas y lecturas: acotadas
- No recorras ni leas el repo entero. Parte de lo concreto: el fichero pedido, un vecino de la
  misma capa, un símbolo conocido.
- `Glob`/`Grep` siempre acotados a un subdirectorio y tipo de fichero. Nada de `**/*` desde la
  raíz ni patrones genéricos (`.*`, `class`, `import`). Si devuelven cientos de resultados,
  afina el patrón; no los leas todos.
- Ignora `target/`, `node_modules/`, `dist/`, `build/`, `.git/`, `.svn/`, binarios y generados.
  Ficheros grandes: por rangos.
- Análisis amplio ("revisa todo el código", auditorías, migraciones masivas): no lo hagas por
  tu cuenta; propón un plan por partes y espera confirmación.
- Nunca fuera del repo: ni rutas del sistema, ni el home, ni otros repositorios.

## Palabras clave peligrosas
No las uses como patrón de `Grep`, nombre de `Glob` ni argumento de comando:
`password`, `passwd`, `secret`, `apikey`, `token`, `bearer`, `credential(s)`, `keystore`,
`private key`, `id_rsa`, `*.pfx`, `*.p12`, `*.jks`, `*.pem`, `*.key`, `.env`, `.npmrc`,
`settings.xml`, cadenas de conexión con usuario/clave.

- En el código busca solo términos funcionales del dominio.
- Si el usuario te pide buscar algo de esa lista: no lo ejecutes, explica la restricción.
- Si encuentras un secreto sin buscarlo: no lo copies ni lo uses; avisa del fichero.

## Prohibido
- Leer, imprimir, volcar o enviar credenciales; saltarse los bloqueos de permisos.
- Descargar y ejecutar código remoto, ofuscar comandos, `Start-Process`, tocar variables de
  sistema, políticas de ejecución o reglas del EDR/AV.
- Enviar contenido del repo a servicios externos.
- **Pedir elevación o buscar una ruta alternativa para esquivar un bloqueo.** La máquina no
  tiene permisos de administrador; si un comando o instalación devuelve «Acceso denegado», es
  el EDR. Para, déjalo como estaba y avisa: no renombres binarios ni cambies la ruta de
  ejecución para que pase.
- **Tocar la base de datos**: migraciones, clientes SQL, levantar la app contra una BD,
  perfiles de entorno con BD (`desa`, `pre`, `pro`). Puedes escribir scripts de migración
  como ficheros; aplicarlos lo hace siempre el usuario.
- Commit, merge, push, tag o despliegue (`svn` o `git`) sin petición explícita.

Si una tarea parece requerir credenciales: no la ejecutes, pregunta. Si no has podido
verificar algo, dilo; no afirmes que funciona.

## Entorno Java / Maven
- JDKs en `%USERPROFILE%\.jdks` (localízalos con `Glob`). Cada proyecto exige su versión:
  mira `pom.xml` (`maven.compiler.*`) o `.java-version`. Maven en el `PATH`.
- No modifiques `JAVA_HOME` desde el shell. Comprueba con `java -version`; si no coincide con
  el proyecto, para y pide al usuario relanzar opencode con `JAVA_HOME` correcto.
- Maven con `workdir` en el módulo, sin `Set-Location` ni rutas fuera del repo.
