import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  Clock3,
  Download,
  File,
  FileText,
  FolderOpen,
  History,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
  WandSparkles,
} from 'lucide-react';
import * as api from '../lib/api';
import type { WikiFile, WikiInfo, WikiLintResult, WikiOperation, WikiRevision, WikiSource } from '../types';
import { AppDialog } from '../components/common/AppDialog';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { UserMenu } from '../components/common/UserMenu';
import { showCaughtError } from '../stores/useErrorDialogStore';

type FileView = 'raw' | 'wiki';

const WIKI_SOURCE_ACCEPT = '.txt,.md,.markdown,.csv,.json,.xml,.html,.htm,.pdf,.docx,.pptx,.xls,.xlsx,.msg,.eml,.png,.jpg,.jpeg,.webp,.gif';
const WIKI_SOURCE_SUFFIX = /\.(txt|md|markdown|csv|json|xml|html|htm|pdf|docx|pptx|xls|xlsx|msg|eml|png|jpe?g|webp|gif)$/i;

export function Wiki() {
  const navigate = useNavigate();
  const uploadRef = useRef<HTMLInputElement>(null);
  const [info, setInfo] = useState<WikiInfo | null>(null);
  const [sources, setSources] = useState<WikiSource[]>([]);
  const [files, setFiles] = useState<WikiFile[]>([]);
  const [operations, setOperations] = useState<WikiOperation[]>([]);
  const [revisions, setRevisions] = useState<WikiRevision[]>([]);
  const [selectedPath, setSelectedPath] = useState('wiki/index.md');
  const [preview, setPreview] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [view, setView] = useState<FileView>('wiki');
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<WikiSource | null>(null);
  const [renameTarget, setRenameTarget] = useState<WikiSource | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<WikiRevision | null>(null);
  const [lint, setLint] = useState<WikiLintResult | null>(null);
  const [linting, setLinting] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [maintaining, setMaintaining] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextInfo, nextSources, nextFiles, nextOperations, nextRevisions] = await Promise.all([
        api.getWiki(),
        api.listWikiSources(),
        api.listWikiTree(),
        api.listWikiOperations(),
        api.listWikiRevisions(),
      ]);
      setInfo(nextInfo);
      setSources(nextSources);
      setFiles(nextFiles);
      setOperations(nextOperations);
      setRevisions(nextRevisions);
      if (!nextFiles.some((file) => file.path === selectedPath)) {
        setSelectedPath(nextFiles.find((file) => file.path === 'wiki/index.md')?.path ?? nextFiles[0]?.path ?? '');
      }
    } catch (error) {
      showCaughtError(error, '读取 Wiki 失败', 'Wiki 加载失败');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [selectedPath]);

  useEffect(() => {
    void load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const active = operations.some((operation) => operation.status === 'pending' || operation.status === 'running');
    if (!active) return;
    const timer = window.setInterval(() => void load(true), 2200);
    return () => window.clearInterval(timer);
  }, [load, operations]);

  const selectedFile = files.find((file) => file.path === selectedPath) ?? null;
  useEffect(() => {
    if (!selectedFile || !isTextFile(selectedFile.path)) {
      setPreview('');
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    void api.getWikiFileContent(selectedFile.path)
      .then((result) => {
        if (!cancelled) setPreview(result.content);
      })
      .catch((error) => {
        if (!cancelled) setPreview(`无法预览：${error instanceof Error ? error.message : '读取失败'}`);
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedFile?.path]);

  const compiledFiles = useMemo(() => files.filter((file) => file.path.startsWith('wiki/')), [files]);
  const currentRevision = revisions.find((revision) => revision.current);

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []);
    event.target.value = '';
    if (!picked.length) return;
    const allowed = picked.filter((file) => WIKI_SOURCE_SUFFIX.test(file.name));
    if (allowed.length !== picked.length) {
      showCaughtError(new Error('Wiki 只接受可转换的文档和图片，不接受压缩包或其他无法解析的格式'), '上传 Wiki 文件失败', '上传失败');
    }
    if (!allowed.length) return;
    setUploading(true);
    try {
      for (const file of allowed) await api.uploadWikiSource(file);
      await load(true);
    } catch (error) {
      showCaughtError(error, '上传 Wiki 文件失败', '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const runMaintenance = async () => {
    if (!instruction.trim()) return;
    setMaintaining(true);
    try {
      await api.maintainWiki(instruction.trim());
      setInstruction('');
      await load(true);
    } catch (error) {
      showCaughtError(error, '提交维护指令失败', 'Wiki 维护失败');
    } finally {
      setMaintaining(false);
    }
  };

  const runLint = async () => {
    setLinting(true);
    try {
      setLint(await api.lintWiki());
    } catch (error) {
      showCaughtError(error, '检查 Wiki 失败', 'Wiki 检查失败');
    } finally {
      setLinting(false);
    }
  };

  if (loading || !info) {
    return <div className="grid min-h-full place-items-center bg-[#f6f4ef] text-sm text-black/45">正在打开 Wiki…</div>;
  }

  return (
    <div className="min-h-full bg-[#f6f4ef] text-[#0b0b0f]">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-black/5 bg-white/80 px-6 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <button type="button" onClick={() => navigate('/')} className="rounded-full p-2 text-black/55 hover:bg-black/5" aria-label="返回首页">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5" />
            <h1 className="text-lg font-semibold tracking-tight">Wiki</h1>
          </div>
          <span className="hidden rounded-full bg-black/[0.05] px-2.5 py-1 text-[11px] text-black/50 sm:inline">{info.source_count} 个来源 · {formatBytes(info.total_size)}</span>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setSettingsOpen(true)} className="rounded-full p-2 text-black/55 hover:bg-black/5" aria-label="Wiki 设置">
            <Settings2 className="h-4 w-4" />
          </button>
          <UserMenu iconClassName="w-5 h-5" />
        </div>
      </header>

      <div className="hidden h-[calc(100vh-64px)] grid-cols-[280px_minmax(0,1fr)_320px] gap-px bg-black/[0.06] md:grid">
        <aside className="flex min-h-0 flex-col bg-[#fbfaf7]">
          <div className="border-b border-black/[0.06] p-4">
            <button type="button" onClick={() => uploadRef.current?.click()} disabled={uploading} className="flex h-10 w-full items-center justify-center gap-2 rounded-full bg-black text-sm font-medium text-white hover:bg-black/85 disabled:opacity-50">
              {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {uploading ? '正在加入队列…' : '添加原始文件'}
            </button>
            <input ref={uploadRef} type="file" multiple accept={WIKI_SOURCE_ACCEPT} className="hidden" onChange={(event) => void upload(event)} />
            <p className="mt-2 text-center text-[11px] leading-4 text-black/40">Markdown / Office / PDF / 文本 / 图片 · 单文件 20 MB · 总容量 500 MB</p>
          </div>
          <div className="p-3">
            <div className="grid grid-cols-2 rounded-full bg-black/[0.05] p-1 text-xs">
              <button type="button" onClick={() => setView('raw')} className={`rounded-full px-3 py-2 ${view === 'raw' ? 'bg-white font-medium shadow-sm' : 'text-black/50'}`}>Raw</button>
              <button type="button" onClick={() => setView('wiki')} className={`rounded-full px-3 py-2 ${view === 'wiki' ? 'bg-white font-medium shadow-sm' : 'text-black/50'}`}>Wiki</button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
            {view === 'raw' ? (
              <div className="space-y-1">
                {sources.map((source) => (
                  <div key={source.id} className={`group flex items-start gap-2 rounded-xl px-2.5 py-2.5 ${selectedPath === `raw/${source.path}` ? 'bg-black text-white' : 'hover:bg-black/[0.035]'}`}>
                    <File className={`mt-0.5 h-4 w-4 shrink-0 ${selectedPath === `raw/${source.path}` ? 'text-white/50' : 'text-black/35'}`} />
                    <button type="button" onClick={() => setSelectedPath(`raw/${source.path}`)} className="min-w-0 flex-1 text-left">
                      <div className={`truncate text-xs font-medium ${selectedPath === `raw/${source.path}` ? 'text-white' : 'text-black/75'}`}>{source.path}</div>
                      <div className={`mt-1 text-[10px] ${selectedPath === `raw/${source.path}` ? 'text-white/55' : source.status === 'failed' ? 'text-red-600' : source.status === 'unsupported' ? 'text-amber-600' : 'text-black/35'}`}>
                        {sourceStatus(source.status)} · {formatBytes(source.size)}
                      </div>
                    </button>
                    <button type="button" onClick={() => setRenameTarget(source)} className={`rounded-full p-1 opacity-0 group-hover:opacity-100 ${selectedPath === `raw/${source.path}` ? 'text-white/45 hover:bg-white/10 hover:text-white' : 'text-black/25 hover:bg-black/5 hover:text-black'}`} aria-label={`重命名 ${source.path}`}>
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" onClick={() => setDeleteTarget(source)} className={`rounded-full p-1 opacity-0 group-hover:opacity-100 ${selectedPath === `raw/${source.path}` ? 'text-white/45 hover:bg-white/10 hover:text-white' : 'text-black/25 hover:bg-red-50 hover:text-red-600'}`} aria-label={`删除 ${source.path}`}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
                {!sources.length ? <EmptyFiles text="尚未添加原始文件" /> : null}
              </div>
            ) : (
              <div className="space-y-0.5">
                <div className="mb-2 flex items-center gap-2 px-2 text-[10px] font-medium uppercase tracking-[0.16em] text-black/35"><FolderOpen className="h-3.5 w-3.5" /> compiled wiki</div>
                {compiledFiles.map((file) => (
                  <button key={file.path} type="button" onClick={() => setSelectedPath(file.path)} className={`flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs transition ${selectedPath === file.path ? 'bg-black text-white' : 'text-black/65 hover:bg-black/[0.04]'}`}>
                    <FileText className="h-3.5 w-3.5 shrink-0 opacity-60" />
                    <span className="truncate" style={{ paddingLeft: `${Math.max(0, file.path.split('/').length - 2) * 8}px` }}>{file.path.replace(/^wiki\//, '')}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        <main className="min-w-0 overflow-y-auto bg-white">
          <div className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-black/[0.06] bg-white/90 px-6 backdrop-blur">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{selectedFile?.path ?? '选择一个 Wiki 文件'}</div>
              {selectedFile ? <div className="mt-0.5 text-[10px] text-black/35">{formatBytes(selectedFile.size)} · {selectedFile.sha256.slice(0, 10)}</div> : null}
            </div>
            {selectedFile ? <a href={selectedFile.download_url} className="rounded-full p-2 text-black/45 hover:bg-black/5 hover:text-black" aria-label="下载"><Download className="h-4 w-4" /></a> : null}
          </div>
          <div className="mx-auto max-w-4xl px-10 py-12">
            {previewLoading ? (
              <div className="flex items-center gap-2 text-sm text-black/40"><Loader2 className="h-4 w-4 animate-spin" />正在读取…</div>
            ) : selectedFile && isMarkdownFile(selectedFile.path) ? (
              <article className="wiki-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview}</ReactMarkdown>
              </article>
            ) : selectedFile && isTextFile(selectedFile.path) ? (
              <pre className="whitespace-pre-wrap break-words rounded-2xl bg-[#f7f6f2] p-5 font-mono text-xs leading-6 text-black/70">{preview}</pre>
            ) : selectedFile && isImageFile(selectedFile.path) ? (
              <img src={`${selectedFile.download_url}&inline=true`} alt={selectedFile.path} className="mx-auto max-h-[70vh] max-w-full rounded-2xl border border-black/10 object-contain" />
            ) : selectedFile && selectedFile.path.toLowerCase().endsWith('.pdf') ? (
              <iframe src={`${selectedFile.download_url}&inline=true`} title={selectedFile.path} className="h-[72vh] w-full rounded-2xl border border-black/10 bg-white" />
            ) : selectedFile ? (
              <div className="rounded-3xl border border-dashed border-black/10 bg-[#faf9f6] px-8 py-16 text-center">
                <File className="mx-auto h-8 w-8 text-black/25" />
                <div className="mt-4 text-sm font-medium">当前格式不支持页面内预览</div>
                <p className="mt-2 text-xs text-black/45">原始文件仍会保留，可下载或由工作流中的 Agent 按需读取。</p>
                <a href={selectedFile.download_url} className="mt-5 inline-flex items-center gap-2 rounded-full bg-black px-4 py-2 text-xs font-medium text-white"><Download className="h-3.5 w-3.5" />下载文件</a>
              </div>
            ) : (
              <EmptyFiles text="选择左侧文件查看内容" />
            )}
          </div>
        </main>

        <aside className="min-h-0 overflow-y-auto bg-[#fbfaf7] p-4">
          <section className="rounded-2xl border border-black/[0.07] bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold"><WandSparkles className="h-4 w-4" /> Maintainer</div>
              <button type="button" onClick={() => void load(true)} className="rounded-full p-1.5 text-black/35 hover:bg-black/5"><RefreshCw className="h-3.5 w-3.5" /></button>
            </div>
            <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：把部署相关内容单独整理成主题页" className="mt-3 min-h-20 w-full resize-none rounded-xl border border-black/10 bg-[#faf9f6] px-3 py-2 text-xs leading-5 outline-none focus:border-black/25" />
            <button type="button" disabled={!instruction.trim() || maintaining} onClick={() => void runMaintenance()} className="mt-2 h-9 w-full rounded-full bg-black text-xs font-medium text-white disabled:opacity-40">{maintaining ? '提交中…' : '开始维护'}</button>
          </section>

          <RightSection title="活动" icon={<Clock3 className="h-4 w-4" />}>
            <div className="space-y-2">
              {operations.slice(0, 8).map((operation) => <OperationRow key={operation.id} operation={operation} onRetry={async () => { await api.retryWikiOperation(operation.id); await load(true); }} onCancel={async () => { await api.cancelWikiOperation(operation.id); await load(true); }} />)}
              {!operations.length ? <div className="text-xs text-black/35">暂无维护活动</div> : null}
            </div>
          </RightSection>

          <RightSection title="质量检查" icon={<ShieldCheck className="h-4 w-4" />}>
            <button type="button" onClick={() => void runLint()} disabled={linting} className="h-9 w-full rounded-full border border-black/10 bg-white text-xs font-medium hover:bg-black/[0.03] disabled:opacity-50">{linting ? '检查中…' : '运行 lint'}</button>
            {lint ? <div className={`mt-3 rounded-xl p-3 text-xs ${lint.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'}`}>{lint.ok ? '结构与完整性检查通过' : `${lint.issues.length} 项需要处理`}</div> : null}
          </RightSection>

          <RightSection title="版本" icon={<History className="h-4 w-4" />}>
            <div className="space-y-2">
              {revisions.slice(0, 8).map((revision) => (
                <button key={revision.id} type="button" disabled={revision.current} onClick={() => setRestoreTarget(revision)} className="w-full rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-left hover:border-black/15 disabled:cursor-default">
                  <div className="flex items-center justify-between gap-2"><span className="truncate text-xs font-medium">{revision.message}</span>{revision.current ? <span className="text-[10px] text-emerald-600">当前</span> : <MoreHorizontal className="h-3.5 w-3.5 text-black/25" />}</div>
                  <div className="mt-1 text-[10px] text-black/35">{formatDate(revision.created_at)} · {revision.file_count} 文件</div>
                </button>
              ))}
            </div>
          </RightSection>
        </aside>
      </div>

      <div className="grid min-h-[calc(100vh-64px)] place-items-center px-8 text-center md:hidden">
        <div><BookOpen className="mx-auto h-8 w-8 text-black/25" /><h2 className="mt-4 font-semibold">Wiki 管理目前仅支持桌面端</h2><p className="mt-2 text-sm leading-6 text-black/45">手机运行仍会遵守 Wiki 授权与冻结规则。</p></div>
      </div>

      <WikiSettingsDialog open={settingsOpen} info={info} onClose={() => setSettingsOpen(false)} onSaved={async () => { setSettingsOpen(false); await load(true); }} />
      <RenameSourceDialog source={renameTarget} onClose={() => setRenameTarget(null)} onSaved={async () => { setRenameTarget(null); await load(true); }} />
      <ConfirmDialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="删除原始文件？" description="Maintainer 会生成新版本并移除对应内容；历史版本仍可恢复。" confirmLabel="删除" tone="danger" onConfirm={async () => { if (!deleteTarget) return; try { await api.deleteWikiSource(deleteTarget.id); setDeleteTarget(null); await load(true); } catch (error) { showCaughtError(error, '删除 Wiki 文件失败', '删除失败'); } }} />
      <ConfirmDialog open={restoreTarget !== null} onClose={() => setRestoreTarget(null)} title="恢复这个 Wiki 版本？" description={restoreTarget ? `${formatDate(restoreTarget.created_at)} · ${restoreTarget.message}` : undefined} confirmLabel="恢复" onConfirm={async () => { if (!restoreTarget) return; try { await api.restoreWikiRevision(restoreTarget.id); setRestoreTarget(null); await load(true); } catch (error) { showCaughtError(error, '恢复 Wiki 版本失败', '恢复失败'); } }} />
      <span className="sr-only">当前版本 {currentRevision?.id}</span>
    </div>
  );
}

function WikiSettingsDialog({ open, info, onClose, onSaved }: { open: boolean; info: WikiInfo; onClose(): void; onSaved(): Promise<void> }) {
  const [purpose, setPurpose] = useState(info.purpose);
  const [schema, setSchema] = useState(info.schema);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setPurpose(info.purpose); setSchema(info.schema); } }, [info.purpose, info.schema, open]);
  return <AppDialog open={open} onClose={onClose} title="Wiki 说明" description="Maintainer 会以这些约束组织长期内容。" footer={<><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-4 py-2 text-sm">取消</button><button type="button" disabled={busy || !purpose.trim() || !schema.trim()} onClick={() => void (async () => { setBusy(true); try { await api.updateWiki({ purpose, schema }); await onSaved(); } catch (error) { showCaughtError(error, '保存 Wiki 设置失败', '保存失败'); } finally { setBusy(false); } })()} className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '保存中…' : '保存'}</button></>}><label className="block text-xs font-medium text-black/55">用途<textarea value={purpose} onChange={(event) => setPurpose(event.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-black/10 px-3 py-2 text-sm leading-6 outline-none focus:border-black/25" /></label><label className="mt-4 block text-xs font-medium text-black/55">结构说明<textarea value={schema} onChange={(event) => setSchema(event.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-black/10 px-3 py-2 text-sm leading-6 outline-none focus:border-black/25" /></label></AppDialog>;
}

function RenameSourceDialog({ source, onClose, onSaved }: { source: WikiSource | null; onClose(): void; onSaved(): Promise<void> }) {
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setPath(source?.path ?? ''); }, [source]);
  return <AppDialog open={source !== null} onClose={onClose} title="重命名原始文件" description="保留 POSIX 相对路径，例如 handbook/policy.pdf。" footer={<><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-4 py-2 text-sm">取消</button><button type="button" disabled={busy || !path.trim() || path === source?.path} onClick={() => void (async () => { if (!source) return; setBusy(true); try { await api.renameWikiSource(source.id, path.trim()); await onSaved(); } catch (error) { showCaughtError(error, '重命名 Wiki 文件失败', '重命名失败'); } finally { setBusy(false); } })()} className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '保存中…' : '保存'}</button></>}><input value={path} onChange={(event) => setPath(event.target.value)} className="h-11 w-full rounded-xl border border-black/10 px-3 text-sm outline-none focus:border-black/25" autoFocus /></AppDialog>;
}

function RightSection({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return <section className="mt-4 border-t border-black/[0.06] pt-4"><div className="mb-3 flex items-center gap-2 text-xs font-semibold text-black/65">{icon}{title}</div>{children}</section>;
}

function OperationRow({ operation, onRetry, onCancel }: { operation: WikiOperation; onRetry(): Promise<void>; onCancel(): Promise<void> }) {
  const active = operation.status === 'pending' || operation.status === 'running';
  return <div className="rounded-xl border border-black/[0.06] bg-white p-3"><div className="flex items-center gap-2">{active ? <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" /> : operation.status === 'success' ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : <Clock3 className="h-3.5 w-3.5 text-amber-600" />}<span className="flex-1 text-xs font-medium">{operationLabel(operation.kind)}</span><span className="text-[10px] text-black/35">{operationStatus(operation.status)}</span></div>{operation.error ? <div className="mt-2 text-[10px] leading-4 text-red-600">{operation.error}</div> : null}<div className="mt-2 flex gap-3">{operation.status === 'failed' ? <button type="button" onClick={() => void onRetry()} className="text-[10px] font-medium text-black underline underline-offset-2">重试</button> : null}{active ? <button type="button" onClick={() => void onCancel()} className="text-[10px] font-medium text-red-600 underline underline-offset-2">取消</button> : null}</div></div>;
}

function EmptyFiles({ text }: { text: string }) { return <div className="py-12 text-center text-xs text-black/35">{text}</div>; }
function isMarkdownFile(path: string) { return /\.md|\.markdown$/i.test(path); }
function isTextFile(path: string) { return /\.(md|markdown|txt|json|csv|html|htm|xml)$/i.test(path); }
function isImageFile(path: string) { return /\.(png|jpe?g|gif|webp)$/i.test(path); }
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 ** 2).toFixed(1)} MB`; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date); }
function sourceStatus(value: WikiSource['status']) { return ({ pending: '等待入库', ready: '已入库', unsupported: '仅保存', failed: '失败', pending_delete: '等待删除' } as const)[value]; }
function operationStatus(value: WikiOperation['status']) { return ({ pending: '等待中', running: '执行中', success: '完成', failed: '失败', cancelled: '已取消' } as const)[value]; }
function operationLabel(value: string) { return ({ ingest: '自动入库', maintenance: '维护 Wiki', delete: '删除来源', rename: '同步重命名' } as Record<string, string>)[value] ?? value; }
