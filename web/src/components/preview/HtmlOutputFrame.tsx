// HtmlOutputFrame：把 output 节点产出的 HTML 字符串渲染到一个跨源 sandbox iframe 内，
// 隔离脚本与同源 API，并通过 postMessage 让 iframe 内部尺寸驱动外部高度自适应。
// iframe 自身不滚动，滚动统一交给外层 Preview/App View/Mobile Run 容器。

import { useEffect, useRef, useState } from 'react';

interface HtmlOutputFrameProps {
  html: string;
  className?: string;
  title?: string;
}

const SANDBOX = 'allow-scripts allow-popups allow-downloads';
const RESIZE_MESSAGE = 'mira-output-resize';

// 注入到 iframe 内的尺寸广播脚本：监听 body 内容高度变化并通知父页。
const SIZE_REPORTER = `<script>(function(){
  function applyNoScroll(){
    var roots = [document.documentElement, document.body];
    for (var i = 0; i < roots.length; i += 1) {
      var root = roots[i];
      if (!root || !root.style) continue;
      root.style.setProperty('overflow', 'hidden', 'important');
      root.style.setProperty('overflow-x', 'hidden', 'important');
      root.style.setProperty('overflow-y', 'hidden', 'important');
    }
  }
  function numberValue(value){
    return Number.isFinite(value) ? value : 0;
  }
  function px(value){
    var parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function cappedSpace(value){
    return Math.min(Math.max(px(value), 0), 160);
  }
  function hasDirectText(element){
    for (var i = 0; i < element.childNodes.length; i += 1) {
      var node = element.childNodes[i];
      if (node.nodeType === Node.TEXT_NODE && node.textContent && node.textContent.trim()) {
        return true;
      }
    }
    return false;
  }
  function isMeasurableContent(element, tagName){
    if (
      tagName === 'td' ||
      tagName === 'th' ||
      tagName === 'li' ||
      tagName === 'p' ||
      tagName === 'pre' ||
      tagName === 'code' ||
      tagName === 'blockquote' ||
      tagName === 'summary' ||
      tagName === 'h1' ||
      tagName === 'h2' ||
      tagName === 'h3' ||
      tagName === 'h4' ||
      tagName === 'h5' ||
      tagName === 'h6' ||
      tagName === 'img' ||
      tagName === 'svg' ||
      tagName === 'canvas' ||
      tagName === 'video' ||
      tagName === 'iframe' ||
      tagName === 'input' ||
      tagName === 'textarea' ||
      tagName === 'select' ||
      tagName === 'button'
    ) {
      return true;
    }
    return hasDirectText(element);
  }
  function bodyContentHeight(body){
    var bodyRect = body.getBoundingClientRect();
    var style = window.getComputedStyle(body);
    var maxBottom = 0;
    var elements = body.querySelectorAll('*');
    for (var i = 0; i < elements.length; i += 1) {
      var child = elements[i];
      var tagName = child.tagName ? child.tagName.toLowerCase() : '';
      if (tagName === 'script' || tagName === 'style' || tagName === 'link' || tagName === 'meta') {
        continue;
      }
      var childStyle = window.getComputedStyle(child);
      if (childStyle.display === 'none' || childStyle.visibility === 'hidden') {
        continue;
      }
      if (!isMeasurableContent(child, tagName)) {
        continue;
      }
      var rect = child.getBoundingClientRect();
      maxBottom = Math.max(maxBottom, rect.bottom - bodyRect.top + cappedSpace(childStyle.marginBottom));
    }
    if (maxBottom > 0) {
      return Math.ceil(maxBottom + cappedSpace(style.paddingBottom));
    }
    return 0;
  }
  function measureHeight(){
    var body = document.body;
    if (!body) {
      return Math.ceil(Math.max(
        numberValue(document.documentElement ? document.documentElement.scrollHeight : 0),
        numberValue(document.documentElement ? document.documentElement.offsetHeight : 0)
      ));
    }
    var contentHeight = bodyContentHeight(body);
    if (contentHeight > 0) {
      return contentHeight;
    }
    var rect = body.getBoundingClientRect();
    return Math.ceil(Math.max(
      numberValue(body.scrollHeight),
      numberValue(body.offsetHeight),
      numberValue(rect.height)
    ));
  }
  function post(){
    try {
      applyNoScroll();
      var h = measureHeight();
      parent.postMessage({ type: '${RESIZE_MESSAGE}', height: h }, '*');
    } catch (e) {}
  }
  applyNoScroll();
  if ('ResizeObserver' in window) {
    var ro = new ResizeObserver(function(){ post(); });
    ro.observe(document.documentElement);
    if (document.body) ro.observe(document.body);
  } else {
    window.addEventListener('resize', post);
  }
  document.addEventListener('DOMContentLoaded', post);
  window.addEventListener('load', post);
  setTimeout(post, 50);
  setTimeout(post, 250);
  setTimeout(post, 1000);
})();</script>`;

function injectReporter(html: string): string {
  if (!html) return '';
  // 尝试在 </body> 前插入；否则直接 append。
  const idx = html.lastIndexOf('</body>');
  if (idx >= 0) {
    return html.slice(0, idx) + SIZE_REPORTER + html.slice(idx);
  }
  return html + SIZE_REPORTER;
}

export function HtmlOutputFrame({ html, className, title = '输出预览' }: HtmlOutputFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState<number>(0);

  // 每次 html 变化清空旧高度，避免上次内容残留。
  useEffect(() => {
    setHeight(0);
  }, [html]);

  // 监听来自 iframe 的尺寸消息。
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if ((data as { type?: unknown }).type !== RESIZE_MESSAGE) return;
      const ifr = iframeRef.current;
      if (!ifr || event.source !== ifr.contentWindow) return;
      const next = (data as { height?: unknown }).height;
      if (typeof next === 'number' && Number.isFinite(next) && next > 0) {
        const measured = Math.ceil(next);
        setHeight((current) => (current === measured ? current : measured));
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const srcDoc = injectReporter(html);

  return (
    <iframe
      ref={iframeRef}
      title={title}
      sandbox={SANDBOX}
      scrolling="no"
      srcDoc={srcDoc}
      className={className ?? 'block w-full rounded-lg border-0 bg-white'}
      style={{
        width: '100%',
        minHeight: '4rem',
        height: height ? `${height}px` : undefined,
        overflow: 'hidden',
      }}
    />
  );
}
