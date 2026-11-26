// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadSettings, resolvePaths, saveCredential } from "../src/config/index.js";
import type { ModelChunk, ModelClient, ModelRequest } from "../src/contracts/index.js";
import { defaultModel } from "../src/models/model-default.js";
import { providerOf } from "../src/models/providers.js";

const model = "LongCat-2.5";
const key = "local-test-longcat-key";

test("LongCat loads from its own key, with stored credentials as fallback", async () => {
  const home = await mkdtemp(join(tmpdir(), "tradingagents-longcat-"));
  try {
    const env = { TRADINGAGENTS_HOME: home, TRADINGAGENTS_MODEL: `longcat/${model}` };
    const missing = await loadSettings(env);
    assert.equal(missing.model, undefined);
    assert.match(missing.problems.join("\n"), /LONGCAT_API_KEY/);

    await saveCredential("longcat", "local-test-stored-key", resolvePaths(env));
    const stored = await loadSettings(env);
    assert.equal(stored.keyFrom.longcat, "stored");
    assert.equal(stored.model && "apiKey" in stored.model && stored.model.apiKey, "local-test-stored-key");

    const configured = await loadSettings({ ...env, LONGCAT_API_KEY: key });
    assert.deepEqual(configured.model, { provider: "longcat", model, apiKey: key });
    assert.deepEqual(defaultModel(configured.connected), { model: env.TRADINGAGENTS_MODEL });
  } finally {
    await rm(home, { recursive: true, force: true });
  }
});

const tool = {
  name: "quote",
  description: "Look up a quote",
  inputSchema: {
    type: "object",
    properties: { ticker: { type: "string" } },
    required: ["ticker"],
    additionalProperties: false,
  },
};

const request: ModelRequest = {
  system: "Use the quote tool.",
  messages: [{ role: "user", text: "What is the quote?" }],
  tools: [tool],
  maxOutputTokens: 1000,
};
const functionCall = { type: "function_call", id: "fc_1", call_id: "call_1", name: "quote" };
const outputMessage = { type: "message", id: "msg_1", role: "assistant" };
const reasoning = { type: "reasoning", id: "rs_1", summary: [] };

function reply(events: { type: string; [key: string]: unknown }[]): Response {
  const response = { id: "resp_1", object: "response", created_at: 1, model };
  const frames = [
    { type: "response.created", response: { ...response, status: "in_progress" } },
    ...events,
    {
      type: "response.completed",
      response: {
        ...response,
        status: "completed",
        usage: { input_tokens: 17, output_tokens: 8, total_tokens: 25 },
      },
    },
  ];
  return new Response(
    frames
      .map(
        (event, sequence_number) =>
          `event: ${event.type}\ndata: ${JSON.stringify({ ...event, sequence_number })}\n\n`,
      )
      .join(""),
    { headers: { "content-type": "text/event-stream" } },
  );
}

async function collect(client: ModelClient, input: ModelRequest): Promise<ModelChunk[]> {
  const chunks: ModelChunk[] = [];
  for await (const chunk of client.stream(input)) chunks.push(chunk);
  return chunks;
}

test("LongCat streams a tool call and accepts its output in a stateless Responses request", async () => {
  const provider = providerOf({ provider: "longcat", model, apiKey: key, reasoning: "max" });
  assert.equal(await provider.reportedWindow({}), 1_048_576);
  assert.equal(provider.limits.output, 262_144);
  const sent: Record<string, unknown>[] = [];
  const client = provider.client({
    fetch: async (url, init) => {
      assert.equal(String(url), "https://api.longcat.chat/openai/v1/responses");
      assert.equal(new Headers(init?.headers).get("authorization"), `Bearer ${key}`);
      sent.push(JSON.parse(String(init?.body)));
      if (sent.length === 1) {
        return reply([
          { type: "response.output_item.added", output_index: 0, item: reasoning },
          {
            type: "response.reasoning_text.delta",
            item_id: reasoning.id,
            output_index: 0,
            content_index: 0,
            delta: "I need the quote tool.",
          },
          { type: "response.output_item.done", output_index: 0, item: reasoning },
          { type: "response.output_item.added", output_index: 1, item: { ...functionCall, arguments: "" } },
          {
            type: "response.function_call_arguments.delta",
            item_id: "fc_1",
            output_index: 1,
            delta: '{"ticker":"AAPL"}',
          },
          {
            type: "response.output_item.done",
            output_index: 1,
            item: { ...functionCall, arguments: '{"ticker":"AAPL"}', status: "completed" },
          },
        ]);
      }
      return reply([
        { type: "response.output_item.added", output_index: 0, item: outputMessage },
        { type: "response.output_text.delta", item_id: "msg_1", output_index: 0, delta: '{"price":100}' },
        { type: "response.output_item.done", output_index: 0, item: outputMessage },
      ]);
    },
  });
  const first = await collect(client, request);
  assert.equal(
    first.some((chunk) => chunk.type === "error"),
    false,
  );
  const call = first.find((chunk) => chunk.type === "tool-call");
  assert.ok(call, JSON.stringify(first));
  assert.equal(call.name, "quote");
  assert.deepEqual(call.args, { ticker: "AAPL" });
  assert.equal(first.at(-1)?.type, "finish");
  const firstBody = sent[0];
  assert.ok(firstBody);
  assert.equal(firstBody.model, model);
  assert.equal(firstBody.stream, true);
  assert.equal(firstBody.max_output_tokens, 1000);
  assert.equal((firstBody.reasoning as { effort: string }).effort, "xhigh");
  assert.deepEqual(firstBody.tools, [
    {
      type: "function",
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema,
      strict: false,
    },
  ]);

  const second = await collect(client, {
    ...request,
    messages: [
      ...request.messages,
      { role: "assistant", parts: [call] },
      { role: "tool", results: [{ callId: call.callId, name: call.name, output: "100" }] },
    ],
    sampling: { thinking: false },
    toolChoice: "none",
    responseFormat: {
      name: "quote_result",
      schema: { type: "object", properties: { price: { type: "number" } }, required: ["price"] },
    },
  });
  const secondBody = sent[1];
  assert.ok(secondBody);
  assert.equal((secondBody.reasoning as { effort: string }).effort, "none");
  const replay = secondBody.input as { type: string; call_id?: string; output?: string }[];
  assert.equal(replay.find((item) => item.type === "function_call")?.call_id, call.callId);
  assert.deepEqual(
    replay.find((item) => item.type === "function_call_output"),
    {
      type: "function_call_output",
      call_id: call.callId,
      output: "100",
    },
  );
  assert.equal((secondBody.text as { format: { type: string } }).format.type, "json_schema");
  for (const field of ["store", "include", "previous_response_id", "tool_choice", "tools"]) {
    assert.equal(field in secondBody, false, field);
  }
  assert.equal(second.find((chunk) => chunk.type === "text" && chunk.text)?.text, '{"price":100}');
  assert.equal(second.at(-1)?.type, "finish");
});

test("LongCat API errors keep the configured key private", async () => {
  const client = providerOf({ provider: "longcat", model, apiKey: key }).client({
    fetch: async () =>
      Response.json({ error: { message: `Refused ${key}`, type: "invalid_api_key" } }, { status: 401 }),
  });
  const [error] = await collect(client, request);
  assert.ok(error?.type === "error");
  assert.equal(error.error.kind, "auth");
  assert.equal(error.error.retryable, false);
  assert.match(error.error.message, /LongCat/);
  assert.equal(error.error.message.includes(key), false);
});
