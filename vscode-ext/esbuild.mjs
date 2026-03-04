import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");

// Bundle 1: Extension host (Node/CJS)
const extConfig = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  platform: "node",
  format: "cjs",
  external: ["vscode"],
  sourcemap: true,
  minify: !watch,
  target: "node18",
};

// Bundle 2: Webview (browser/IIFE) — bundles marked + hljs
const webviewConfig = {
  entryPoints: ["media/chat.js"],
  bundle: true,
  outfile: "dist/chat.js",
  platform: "browser",
  format: "iife",
  sourcemap: true,
  minify: !watch,
  target: "es2020",
};

if (watch) {
  const ctx1 = await esbuild.context(extConfig);
  const ctx2 = await esbuild.context(webviewConfig);
  await Promise.all([ctx1.watch(), ctx2.watch()]);
  console.log("Watching for changes...");
} else {
  await Promise.all([esbuild.build(extConfig), esbuild.build(webviewConfig)]);
  console.log("Build complete.");
}
