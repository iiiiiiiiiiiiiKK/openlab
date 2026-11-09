const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT || 3000);

const page = `<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>OPENLAB Node Fixture</title>
<style>
  body{margin:0;font:15px/1.6 system-ui;background:#0b0c0e;color:#e6e8eb;padding:40px}
  h1{font-size:26px;margin:0 0 6px} .muted{color:#8b95a1}
  button{margin-top:16px;padding:8px 14px;border-radius:8px;border:1px solid #2a2f36;background:#151a20;color:#e6e8eb;cursor:pointer}
  .row{display:flex;gap:8px;margin-top:18px}
  input{padding:8px;border-radius:8px;border:1px solid #2a2f36;background:#101418;color:#e6e8eb}
  #out{margin-top:12px;color:#3ecf8e}
</style></head>
<body>
  <h1>Node fixture app</h1>
  <p class="muted">This page is served by a real Node HTTP server started by OPENLAB.</p>
  <div class="row">
    <input id="name" placeholder="Type a name" />
    <button id="greet">Greet</button>
  </div>
  <div id="out"></div>
  <p><a href="/about">About page</a></p>
  <script>
    document.getElementById("greet").addEventListener("click", () => {
      const v = document.getElementById("name").value || "world";
      document.getElementById("out").textContent = "Hello, " + v + "!";
    });
  </script>
</body></html>`;

const about = `<!doctype html><html><head><meta charset="utf-8"><title>About · Node Fixture</title></head>
<body style="font:15px system-ui;background:#0b0c0e;color:#e6e8eb;padding:40px">
<h1>About</h1><p>Second page for multi-page exploration.</p><a href="/">Back home</a></body></html>`;

const server = http.createServer((req, res) => {
  if (req.url === "/health") { res.writeHead(200, {"content-type": "application/json"}); res.end(JSON.stringify({ok:true})); return; }
  if (req.url === "/about") { res.writeHead(200, {"content-type": "text/html"}); res.end(about); return; }
  res.writeHead(200, {"content-type": "text/html"}); res.end(page);
});

server.listen(PORT, "127.0.0.1", () => console.log("fixture listening on " + PORT));
