import { OpenRouter } from '@openrouter/agent';
import type { Item, InlineHookConfig } from '@openrouter/agent';
import { stepCountIs, maxCost } from '@openrouter/agent/stop-conditions';
import type { AgentConfig } from './config.js';
import { buildTools } from './tools/index.js';
import { buildSystemPrompt } from './system-prompt.js';

export type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string };

export type AgentEvent =
  | { type: 'text'; delta: string }
  | { type: 'tool_call'; name: string; callId: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; callId: string; output: string }
  | { type: 'reasoning'; delta: string };

export interface RunOptions {
  onEvent?: (event: AgentEvent) => void;
  signal?: AbortSignal;
  hooks?: InlineHookConfig;
}

export interface RunResult {
  text: string;
  usage: { inputTokens: number; outputTokens: number; cost?: number };
}

let cachedClient: { key: string; client: OpenRouter } | null = null;

export function getClient(config: AgentConfig): OpenRouter {
  if (cachedClient?.key === config.apiKey) return cachedClient.client;
  const client = new OpenRouter({
    apiKey: config.apiKey,
    httpReferer: config.appUrl,
    appTitle: config.appName,
    // The SDK default keeps retrying connection errors / 5xx for up to an
    // HOUR. In a terminal that looks like a frozen spinner, so cap the
    // window; runAgentWithRetry adds a couple of slower retries on top.
    retryConfig: {
      strategy: 'backoff',
      backoff: { initialInterval: 500, maxInterval: 8000, exponent: 1.5, maxElapsedTime: 20_000 },
      retryConnectionErrors: true,
    },
  });
  cachedClient = { key: config.apiKey, client };
  return client;
}

export async function runAgent(config: AgentConfig, input: string | ChatMessage[], options?: RunOptions): Promise<RunResult> {
  const client = getClient(config);
  const tools = buildTools(config);

  const result = client.callModel({
    model: config.model,
    instructions: buildSystemPrompt(config),
    input: input as string | Item[],
    tools,
    stopWhen: [stepCountIs(config.maxSteps), maxCost(config.maxCost)],
    doomLoop: true,
    ...(options?.hooks && { hooks: options.hooks }),
    ...(options?.signal && { signal: options.signal }),
  });

  if (options?.onEvent) {
    // Track text length PER message item by id. A multi-step agent emits
    // multiple OutputMessage items over the course of a single run (one per
    // assistant turn between tool calls), and each one grows from 0 to its
    // final length. A single global cursor breaks on the second message:
    // when its length is smaller than the cursor from the first, the slice
    // cuts mid-string and drops the start of the new message's text.
    const textByItem = new Map<string, number>();
    const callNames = new Map<string, string>();

    for await (const item of result.getItemsStream()) {
      if (options.signal?.aborted) break;
      if (item.type === 'message') {
        const text =
          item.content
            ?.filter((c): c is { type: 'output_text'; text: string } => 'text' in c)
            .map((c) => c.text)
            .join('') ?? '';
        const prev = textByItem.get(item.id) ?? 0;
        if (text.length > prev) {
          options.onEvent({ type: 'text', delta: text.slice(prev) });
          textByItem.set(item.id, text.length);
        }
      } else if (item.type === 'function_call') {
        callNames.set(item.callId, item.name);
        if (item.status === 'completed') {
          const args = (() => {
            try {
              return item.arguments ? (JSON.parse(item.arguments) as Record<string, unknown>) : {};
            } catch {
              return {};
            }
          })();
          options.onEvent({ type: 'tool_call', name: item.name, callId: item.callId, args });
        }
      } else if (item.type === 'function_call_output') {
        const out = typeof item.output === 'string' ? item.output : JSON.stringify(item.output);
        options.onEvent({
          type: 'tool_result',
          name: callNames.get(item.callId) ?? 'unknown',
          callId: item.callId,
          output: out.length > 200 ? out.slice(0, 200) + '…' : out,
        });
      } else if (item.type === 'reasoning') {
        const text = item.summary?.map((s: { text: string }) => s.text).join('') ?? '';
        if (text) options.onEvent({ type: 'reasoning', delta: text });
      }
    }
  }

  const response = await result.getResponse();
  let usage: RunResult['usage'] = {
    inputTokens: response.usage?.inputTokens ?? 0,
    outputTokens: response.usage?.outputTokens ?? 0,
  };
  // Aggregate across every round of the tool loop when the SDK can tell us.
  try {
    const totals = await result.getUsage();
    usage = {
      inputTokens: totals.inputTokens ?? usage.inputTokens,
      outputTokens: totals.outputTokens ?? usage.outputTokens,
      ...(totals.cost !== undefined && { cost: totals.cost }),
    };
  } catch {
    /* keep the final-round usage */
  }
  return { text: response.outputText ?? '', usage };
}

export async function runAgentWithRetry(
  config: AgentConfig,
  input: string | ChatMessage[],
  options?: RunOptions & { maxRetries?: number },
): Promise<RunResult> {
  const max = options?.maxRetries ?? 2;
  for (let attempt = 0; attempt <= max; attempt++) {
    try {
      return await runAgent(config, input, options);
    } catch (err: any) {
      if (options?.signal?.aborted) throw err;
      const s = err?.status ?? err?.statusCode;
      if (!(s === 429 || (s >= 500 && s < 600)) || attempt === max) throw err;
      await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** attempt, 30000)));
    }
  }
  throw new Error('Unreachable');
}
