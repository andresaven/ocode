import {
  mkdirSync, readFileSync, renameSync, unwatchFile, watchFile, writeFileSync,
} from "node:fs";
import { dirname } from "node:path";

// The source files are read-only mounts outside the working copy. Only the
// effective global instructions are generated, in the container's config dir.
export function syncInstructions({ globalSource, projectSource, target, onError }) {
  const sources = [globalSource, projectSource].filter(Boolean);
  let previous = null;
  function refresh() {
    const contents = sources.map(source =>
      readFileSync(source, "utf8").replace(/^\uFEFF/, ""),
    ).join("\n\n") + "\n\n" +
      "Regla de ocode: las instrucciones globales prevalecen ante contradicciones. " +
      "Para cualquier petición general de crear o editar el AGENTS de este proyecto, usa /etc/ocode/project-AGENTS.md e informa al usuario de esa ruta. " +
      "No crees /workspace/AGENTS.md para esa petición; úsalo solo si se pide expresamente crear o modificar el archivo dentro del repositorio. " +
      "Para instalar skills en esta instancia, usa /etc/ocode/project-skills/<id>/SKILL.md, fuera de /workspace; incluye las skills de las que dependa. " +
      "No instales skills en /workspace ni en .opencode/skills del repositorio como sustituto. " +
      "Las skills globales están montadas en solo lectura: indica al usuario que debe colocarlas en config/skills/<id>/SKILL.md del host. " +
      "Para cualquier tarea con documentos Word (.docx), carga primero la skill docx con ID exacto docx mediante la herramienta skill; aplica a leer, extraer, comparar, crear, editar y validar. " +
      "En contenedores ocode, usa Node.js como runtime preferido y comprueba herramientas Node antes de concluir que no puedes leer un DOCX porque falte Python o el lector directo no lo admita. " +
      "Si la skill no está disponible, detente e informa. Si no hay una herramienta Node adecuada, comprueba herramientas ZIP; si tampoco hay, identifica la dependencia Node mínima y pide autorización para instalarla localmente. No finalices solo diciendo que no puedes ni instales dependencias o cambies de runtime sin autorización. " +
      "Para contrastar documentos con código, limita la inspección al apartado solicitado y al código pertinente; nunca consultes bases de datos. " +
      "Si las reglas o los permisos impiden escribir en la ruta específica, no busques otra vía: informa al usuario de la ruta y los pasos manuales, sin afirmar que quedaron instaladas.";
    if (contents === previous) return;
    mkdirSync(dirname(target), { recursive: true });
    // Atomic replacement avoids OpenCode reading partially written guidance.
    writeFileSync(`${target}.tmp`, contents, { mode: 0o600 });
    renameSync(`${target}.tmp`, target);
    previous = contents;
  }
  refresh(); // A missing/unreadable source must prevent backend startup.
  const changed = () => {
    try {
      refresh();
    } catch (error) {
      onError(error);
    }
  };
  for (const source of sources) {
    watchFile(source, { interval: 250, persistent: false }, changed);
  }
  return () => {
    for (const source of sources) unwatchFile(source, changed);
  };
}
