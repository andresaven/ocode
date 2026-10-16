# Cambios

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
