// 真实 SSE 客户端：连接 GET /api/runs/{run_id}/events。
//
// 协议要点：
// - 通过 fetch + ReadableStream 自行解析 SSE 帧（不用浏览器 EventSource，
//   因为后者不能携带 Authorization header，且对 Last-Event-ID 控制有限）。
// - 收到完整帧后调 onEvent；对未识别的 event 类型静默忽略，避免阻塞主流程。
// - 断流自动重连：把最近一帧的 id 作为 Last-Event-ID 重连；
//   服务端返回 410 时停止重连（buffer 不可重放），由调用方走 GET /api/runs/{id} 兜底。
// - 收到 run.end 后主动 close 流；外部调用 close() 也会标记 closed 并取消 fetch。

import type { App, DecisionOption, RunEvent } from '../types';
import { getToken } from './auth';

export type RunStreamHandler = (evt: RunEvent) => void;

export interface RunStream {
  close(): void;
}

interface OpenOpts {
  runId: string;
  app?: App;
  inputs?: Record<string, unknown>;
  onEvent: RunStreamHandler;
  /** 服务端 410 / 404 后调用，调用方一般做一次 GET /api/runs/{id} 兜底。*/
  onRecover?: () => void | Promise<void>;
}

interface ParsedFrame {
  id?: string;
  event: string;
  data: string;
}

export function openRunStream(opts: OpenOpts): RunStream {
  const controller = new AbortController();
  let closed = false;
  let lastEventId: string | undefined;
  let runEnded = false;

  const close = () => {
    if (closed) return;
    closed = true;
    try {
      controller.abort();
    } catch {
      // ignore
    }
  };

  const loop = async () => {
    while (!closed) {
      try {
        const headers: Record<string, string> = { Accept: 'text/event-stream' };
        const token = getToken();
        if (token) headers.Authorization = `Bearer ${token}`;
        if (lastEventId) headers['Last-Event-ID'] = lastEventId;

        const response = await fetch(`/api/runs/${opts.runId}/events`, {
          method: 'GET',
          headers,
          signal: controller.signal,
          credentials: 'same-origin',
        });

        if (response.status === 410 || response.status === 404) {
          // buffer 已不可重放 / 记录不存在：调用 onRecover 让上层兜底，然后退出。
          if (opts.onRecover) await opts.onRecover();
          close();
          return;
        }
        if (!response.ok || !response.body) {
          // 其它非 2xx：短延迟后重试，最多挂在网络抖动 / 临时 5xx 上。
          await delay(1000);
          continue;
        }
        const replayOnly = response.headers.get('X-Mira-Replay-Only') === 'true';

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let separator = findFrameSeparator(buffer);
          while (separator !== -1) {
            const raw = buffer.slice(0, separator);
            buffer = buffer.slice(separator + framingLength(buffer, separator));
            const frame = parseFrame(raw);
            if (frame) {
              if (frame.id) lastEventId = frame.id;
              const evt = toRunEvent(frame);
              if (evt) {
                opts.onEvent(evt);
                if (evt.event === 'run.end') {
                  runEnded = true;
                }
              }
            }
            separator = findFrameSeparator(buffer);
          }
        }
        if (runEnded || closed) {
          close();
          return;
        }
        if (replayOnly) {
          if (opts.onRecover) await opts.onRecover();
          close();
          return;
        }
        // 服务端关流但未发 run.end（断网 / 反代超时）：短延迟后重连，
        // 用 lastEventId 重放最近未消费的事件。
        await delay(500);
      } catch (error) {
        if (closed) return;
        // AbortError 由 close() 触发，循环已经退出；其它异常做退避重连。
        if ((error as { name?: string })?.name === 'AbortError') return;
        await delay(1500);
      }
    }
  };

  void loop();

  return { close };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// SSE 帧之间用空行分隔；空行的字节序列在 \n\n、\r\n\r\n、\r\r 中都可能出现。
function findFrameSeparator(buffer: string): number {
  const candidates = ['\n\n', '\r\n\r\n', '\r\r'];
  let best = -1;
  for (const sep of candidates) {
    const idx = buffer.indexOf(sep);
    if (idx !== -1 && (best === -1 || idx < best)) best = idx;
  }
  return best;
}

function framingLength(buffer: string, idx: number): number {
  if (buffer.startsWith('\r\n\r\n', idx)) return 4;
  if (buffer.startsWith('\n\n', idx)) return 2;
  if (buffer.startsWith('\r\r', idx)) return 2;
  return 2;
}

function parseFrame(raw: string): ParsedFrame | null {
  if (!raw) return null;
  const lines = raw.split(/\r\n|\r|\n/);
  let id: string | undefined;
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    if (colon === -1) continue;
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, '');
    if (field === 'id') id = value;
    else if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  return { id, event, data: dataLines.join('\n') };
}

function toRunEvent(frame: ParsedFrame): RunEvent | null {
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(frame.data) as Record<string, unknown>;
  } catch {
    return null;
  }
  switch (frame.event) {
    case 'step.start':
      if (typeof payload.node_id === 'string') {
        return { event: 'step.start', node_id: payload.node_id, ts: String(payload.ts ?? '') };
      }
      return null;
    case 'step.log':
      if (typeof payload.node_id === 'string' && payload.log && typeof payload.log === 'object') {
        const log = payload.log as Record<string, unknown>;
        const level = log.level;
        return {
          event: 'step.log',
          node_id: payload.node_id,
          log: {
            ts: String(log.ts ?? ''),
            level:
              level === 'info' || level === 'warn' || level === 'error' || level === 'tool'
                ? level
                : 'info',
            text: String(log.text ?? ''),
          },
        };
      }
      return null;
    case 'step.delta':
      if (typeof payload.node_id === 'string') {
        return {
          event: 'step.delta',
          node_id: payload.node_id,
          chunk: (payload.chunk as RunEvent extends { event: 'step.delta'; chunk: infer C } ? C : never) ?? {
            type: 'text',
          },
        };
      }
      return null;
    case 'step.end':
      if (typeof payload.node_id === 'string') {
        return { event: 'step.end', node_id: payload.node_id, step: payload.step as never };
      }
      return null;
    case 'step.waiting':
      if (typeof payload.node_id === 'string') {
        const request =
          payload.request && typeof payload.request === 'object'
            ? (payload.request as Record<string, unknown>)
            : undefined;
        const context =
          request?.context && typeof request.context === 'object'
            ? (request.context as Record<string, unknown>)
            : undefined;
        if (
          !request ||
          typeof context?.title !== 'string' ||
          typeof context.summary !== 'string' ||
          typeof request.tool_use_id !== 'string'
        ) {
          return null;
        }
        return {
          event: 'step.waiting',
          node_id: payload.node_id,
          question: typeof payload.question === 'string' ? payload.question : undefined,
          request: {
            context: {
              title: context.title,
              summary: context.summary,
            },
            groups: Array.isArray(request.groups)
              ? request.groups.flatMap((item) => {
                  if (!item || typeof item !== 'object') return [];
                  const group = item as Record<string, unknown>;
                  if (
                    typeof group.id !== 'string' ||
                    typeof group.label !== 'string' ||
                    (group.type !== 'single' && group.type !== 'multi') ||
                    !Array.isArray(group.options)
                  ) {
                    return [];
                  }
                  return [{
                    id: group.id,
                    label: group.label,
                    type: group.type,
                    options: normalizeDecisionOptions(group.options),
                    placeholder:
                      typeof group.placeholder === 'string' ? group.placeholder : undefined,
                  }];
                })
              : [],
            tool_use_id: request.tool_use_id,
          },
        };
      }
      return null;
    case 'run.waiting_for_user':
      if (typeof payload.node_id === 'string') {
        return { event: 'run.waiting_for_user', node_id: payload.node_id };
      }
      return null;
    case 'run.end':
      return {
        event: 'run.end',
        status: (payload.status as 'success' | 'failed' | 'cancelled') ?? 'failed',
        error: typeof payload.error === 'string' ? payload.error : undefined,
      };
    default:
      return null;
  }
}

function normalizeDecisionOptions(rawOptions: unknown[]): DecisionOption[] {
  return rawOptions.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const option = item as Record<string, unknown>;
    if (typeof option.label !== 'string' || typeof option.description !== 'string') return [];
    return [{
      label: option.label,
      description: option.description,
      recommended: option.recommended === true,
    }];
  });
}
