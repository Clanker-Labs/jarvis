// Generates ts/events.gen.ts from schema/events.schema.json (written by export_schema.py).
import { readFile, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

const here = new URL("..", import.meta.url);
const schema = JSON.parse(await readFile(new URL("schema/events.schema.json", here), "utf8"));
const ts = await compile(schema, "JarvisEvent", {
  bannerComment:
    "/* eslint-disable */\n/** AUTO-GENERATED from packages/protocol (pydantic). Do not edit — run `make protocol`. */",
  additionalProperties: false,
  unreachableDefinitions: true,
  style: { singleQuote: false, semi: true, printWidth: 100 },
});
await writeFile(new URL("ts/events.gen.ts", here), ts);
console.log("wrote ts/events.gen.ts");
