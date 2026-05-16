import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

const DATA_FILE = resolve(__dirname, "data/business.json");

function makeEtag(content: string): string {
  let h = 0;
  for (let i = 0; i < content.length; i++) h = (h * 31 + content.charCodeAt(i)) >>> 0;
  return `"${h.toString(16)}"`;
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: "business-api",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (req.method === "GET" && req.url === "/business.json") {
            const content = readFileSync(DATA_FILE, "utf-8");
            const etag = makeEtag(content);
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.setHeader("ETag", etag);
            res.setHeader("Cache-Control", "no-cache");
            if (req.headers["if-none-match"] === etag) {
              res.statusCode = 304;
              res.end();
              return;
            }
            res.setHeader("Content-Type", "application/json");
            res.statusCode = 200;
            res.end(content);
            return;
          }

          if (req.method === "POST" && req.url === "/update-phone") {
            let body = "";
            req.on("data", (chunk: Buffer) => { body += chunk.toString(); });
            req.on("end", () => {
              try {
                const { phone } = JSON.parse(body) as { phone: string };
                const data = JSON.parse(readFileSync(DATA_FILE, "utf-8")) as Record<string, string>;
                data.phone = phone;
                const content = JSON.stringify(data, null, 2);
                writeFileSync(DATA_FILE, content, "utf-8");
                const etag = makeEtag(content);
                res.setHeader("Access-Control-Allow-Origin", "*");
                res.setHeader("Content-Type", "application/json");
                res.statusCode = 200;
                res.end(JSON.stringify({ etag }));
              } catch {
                res.statusCode = 400;
                res.end("Bad request");
              }
            });
            return;
          }

          if (req.method === "OPTIONS") {
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
            res.setHeader("Access-Control-Allow-Headers", "Content-Type, If-None-Match");
            res.statusCode = 204;
            res.end();
            return;
          }

          next();
        });
      },
    },
  ],
  server: { port: 5174 },
});
