// 通用工具：跨浏览器 UUID 生成。

export function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return 'id_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
