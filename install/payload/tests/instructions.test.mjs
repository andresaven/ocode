import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { syncInstructions } from "../src/ocode/instructions.mjs";

const here = dirname(fileURLToPath(import.meta.url));

function fixture(t) {
  const root = mkdtempSync(join(here, "instructions-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const globalSource = join(root, "global.md");
  const projectSource = join(root, "project.md");
  const target = join(root, "config", "AGENTS.md");
  writeFileSync(globalSource, "\uFEFFGlobal guidance");
  writeFileSync(projectSource, "Project guidance");
  return { globalSource, projectSource, target };
}

test("effective instructions preserve global and project rules without editing sources", t => {
  const options = fixture(t);
  const stop = syncInstructions({ ...options, onError: assert.fail });
  t.after(stop);
  const effective = readFileSync(options.target, "utf8");
  assert.ok(effective.startsWith("Global guidance\n\nProject guidance\n\n"));
  assert.match(effective, /globales prevalecen/);
  assert.match(effective, /\/etc\/ocode\/project-AGENTS\.md/);
  assert.match(effective, /\/etc\/ocode\/project-skills\/<id>\/SKILL\.md/);
  assert.match(effective, /No instales skills en \/workspace ni en \.opencode\/skills del repositorio/);
  assert.match(effective, /config\/skills\/<id>\/SKILL\.md del host/);
  assert.match(effective, /Para cualquier tarea con documentos Word \(\.docx\), carga primero la skill docx/);
  assert.match(effective, /usa Node\.js como runtime preferido/);
  assert.match(effective, /comprueba herramientas ZIP/);
  assert.match(effective, /pide autorización para instalarla localmente/);
  assert.match(effective, /No finalices solo diciendo que no puedes/);
  assert.match(effective, /nunca consultes bases de datos/);
  assert.match(effective, /informa al usuario de la ruta y los pasos manuales/);
  assert.equal(readFileSync(options.globalSource, "utf8"), "\uFEFFGlobal guidance");
  assert.equal(readFileSync(options.projectSource, "utf8"), "Project guidance");
});

test("disabled project rules do not remove global guidance", t => {
  const options = fixture(t);
  const stop = syncInstructions({ ...options, projectSource: undefined, onError: assert.fail });
  t.after(stop);
  assert.match(readFileSync(options.target, "utf8"), /^Global guidance\n\nRegla de ocode:/);
});

test("missing instructions prevent startup", t => {
  const options = fixture(t);
  unlinkSync(options.globalSource);
  assert.throws(() => syncInstructions({ ...options, onError: assert.fail }), { code: "ENOENT" });
});

test("edits are synchronized and unreadable rules report a failure", { timeout: 5000 }, async t => {
  const options = fixture(t);
  let failure;
  const stop = syncInstructions({ ...options, onError: error => { failure = error; } });
  t.after(stop);
  const waitFor = async check => {
    for (let attempt = 0; attempt < 30; attempt++) {
      if (check()) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail("instruction update not observed");
  };
  // Allow polling to establish the initial source stat.
  await new Promise(resolve => setTimeout(resolve, 300));
  writeFileSync(options.projectSource, "Edited project guidance");
  await waitFor(() => readFileSync(options.target, "utf8").includes("Edited project guidance"));
  writeFileSync(options.globalSource, "Edited global guidance");
  await waitFor(() => readFileSync(options.target, "utf8").startsWith(
    "Edited global guidance\n\nEdited project guidance\n\nRegla de ocode:"));
  unlinkSync(options.projectSource);
  await waitFor(() => failure?.code === "ENOENT");
});
