const http = require("http");
const PORT = Number(process.env.PORT || 3000);
http.createServer((req, res) => {
  res.writeHead(200, {"content-type": "text/html"});
  res.end("<!doctype html><title>Compose fixture</title><h1>Compose multi-service fixture</h1>");
}).listen(PORT, "0.0.0.0");
