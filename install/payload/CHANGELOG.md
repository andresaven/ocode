# Cambios

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

## Próxima versión

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
