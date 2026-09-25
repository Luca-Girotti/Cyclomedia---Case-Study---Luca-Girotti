import express from "express";
import basicAuth from "express-basic-auth";
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: "sk-ant-api03-EXAMPLEONLY00000000000000" });
const app = express();
app.use(express.json());

const admin = basicAuth({ users: { admin: process.env.ADMIN_PASSWORD } });
let requests = 0;

app.post("/translate", async (req, res) => {
  const { text, language } = req.body;
  requests += 1;
  console.log("translate request", req.body);

  const message = await client.messages.create({
    model: "claude-opus-5",
    max_tokens: 2048,
    system: `You translate meeting notes into ${language}. Keep people's names unchanged.`,
    messages: [{ role: "user", content: text }],
  });

  const translation = message.content.find((block) => block.type === "text")?.text ?? "";
  res.send(`<h1>Translation</h1><div>${translation}</div>`);
});

app.get("/usage", admin, (req, res) => {
  res.json({ requests });
});

app.listen(3000);
