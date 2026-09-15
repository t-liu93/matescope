import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { URL } from "node:url";

const root = resolve("dist");
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json; charset=utf-8",
};

createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/test-update-sw.js") {
    const worker = readFileSync(resolve(root, "sw.js"), "utf8").replace("matescope-offline-v2", "matescope-offline-v3");
    response.writeHead(200, { "Content-Type": types[".js"], "Cache-Control": "no-cache" });
    response.end(worker);
    return;
  }
  const requested = resolve(root, `.${decodeURIComponent(url.pathname)}`);
  const file = requested.startsWith(root) && existsSync(requested) && statSync(requested).isFile()
    ? requested
    : resolve(root, "index.html");
  const extension = file.slice(file.lastIndexOf("."));
  response.writeHead(200, { "Content-Type": types[extension] ?? "application/octet-stream" });
  createReadStream(file).pipe(response);
}).listen(49231, "127.0.0.1");
