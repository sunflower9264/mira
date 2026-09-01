import { useEffect, useMemo, useState } from 'react';
import { Clock3, FolderKanban, GitBranch, MoreHorizontal, Plus, Server, Sparkles } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { AppDialog } from '../common/AppDialog';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { PromptDialog } from '../common/PromptDialog';
import { useWorkspaceStore } from '../../stores/useWorkspaceStore';
import { showCaughtError, showErrorDialog } from '../../stores/useErrorDialogStore';
import type { Workspace, WorkspaceCreateInput } from '../../types';

export function WorkspaceLibrary({ mobile = false }: { mobile?: boolean }) {
  const navigate = useNavigate();
  const { workspaces, loading, error, load, create, update, remove } = useWorkspaceStore();
  const [createOpen, setCreateOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Workspace | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Workspace | null>(null);
  const [nextName, setNextName] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (error) showErrorDialog(error, '加载失败');
  }, [error]);

  const routeFor = (workspace: Workspace) =>
    mobile ? `/m/workspaces/${workspace.id}` : `/workspaces/${workspace.id}`;

  return (
    <>
      <section>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className={mobile ? 'text-lg font-semibold tracking-tight' : 'text-xl font-semibold tracking-tight'}>
              工作空间
            </h2>
            <p className="mt-1 text-xs leading-5 text-black/45">
              持久项目、Codex 会话、Wiki 与可视化工作流都在这里协作。
            </p>
          </div>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85"
          >
            <Plus className="h-4 w-4" />
            {mobile ? '新建' : '新建工作空间'}
          </button>
        </div>

        {loading && workspaces.length === 0 ? (
          <div className={`mt-4 grid gap-4 ${mobile ? 'grid-cols-1' : 'sm:grid-cols-2 lg:grid-cols-3'}`}>
            {[0, 1, 2].map((item) => (
              <div key={item} className="h-44 animate-pulse rounded-[22px] bg-neutral-200/65" />
            ))}
          </div>
        ) : workspaces.length === 0 ? (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="mt-4 flex w-full flex-col items-center justify-center rounded-[24px] border border-dashed border-black/15 bg-white/55 px-5 py-14 text-center transition hover:border-black/25 hover:bg-white"
          >
            <Sparkles className="h-7 w-7 text-black/25" />
            <span className="mt-3 text-sm font-medium text-black/70">创建第一个工作空间</span>
            <span className="mt-1 max-w-sm text-xs leading-5 text-black/45">
              从空目录、文件或私有 Git 项目开始，Codex 会在同一个持久目录中继续工作。
            </span>
          </button>
        ) : (
          <div className={`mt-4 grid gap-4 ${mobile ? 'grid-cols-1' : 'sm:grid-cols-2 lg:grid-cols-3'}`}>
            {workspaces.map((workspace) => (
              <WorkspaceCard
                key={workspace.id}
                workspace={workspace}
                onOpen={() => navigate(routeFor(workspace))}
                onRename={() => {
                  setRenameTarget(workspace);
                  setNextName(workspace.name);
                }}
                onDelete={() => setDeleteTarget(workspace)}
              />
            ))}
          </div>
        )}
      </section>

      <CreateWorkspaceDialog
        open={createOpen}
        busy={busy}
        onClose={() => !busy && setCreateOpen(false)}
        onCreate={async (input) => {
          setBusy(true);
          try {
            const workspace = await create(input);
            setCreateOpen(false);
            navigate(routeFor(workspace));
          } catch (error) {
            showCaughtError(error, '创建工作空间失败', '创建失败');
          } finally {
            setBusy(false);
          }
        }}
      />
      <PromptDialog
        open={renameTarget !== null}
        onClose={() => !busy && setRenameTarget(null)}
        onConfirm={async () => {
          if (!renameTarget || !nextName.trim()) return;
          setBusy(true);
          try {
            await update(renameTarget.id, { name: nextName.trim() });
            setRenameTarget(null);
          } catch (error) {
            showCaughtError(error, '重命名工作空间失败', '重命名失败');
          } finally {
            setBusy(false);
          }
        }}
        title="重命名工作空间"
        description="名称只用于识别，不会更改项目目录或 Codex 会话。"
        inputLabel="工作空间名称"
        value={nextName}
        onChange={setNextName}
        disabled={!nextName.trim() || nextName.trim() === renameTarget?.name}
        busy={busy}
      />
      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => !busy && setDeleteTarget(null)}
        onConfirm={async () => {
          if (!deleteTarget) return;
          setBusy(true);
          try {
            await remove(deleteTarget.id);
            setDeleteTarget(null);
          } catch (error) {
            showCaughtError(error, '删除工作空间失败', '删除失败');
          } finally {
            setBusy(false);
          }
        }}
        title="永久删除工作空间？"
        description={deleteTarget ? `“${deleteTarget.name}”的项目文件、运行容器、会话和 Git 凭据会立即永久删除，无法恢复。` : undefined}
        confirmLabel="永久删除"
        tone="danger"
        busy={busy}
      />
    </>
  );
}

function WorkspaceCard({
  workspace,
  onOpen,
  onRename,
  onDelete,
}: {
  workspace: Workspace;
  onOpen(): void;
  onRename(): void;
  onDelete(): void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const runtime = runtimeCopy(workspace.runtime_status);
  return (
    <article className="relative min-h-44 overflow-hidden rounded-[22px] border border-black/5 bg-white p-5 shadow-card transition hover:-translate-y-0.5 hover:shadow-[0_10px_30px_rgba(0,0,0,0.08)]">
      <button type="button" onClick={onOpen} className="absolute inset-0 z-0" aria-label={`打开 ${workspace.name}`} />
      <div className="relative z-10 flex items-start justify-between gap-3 pointer-events-none">
        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-black text-white shadow-sm">
          <FolderKanban className="h-5 w-5" />
        </div>
        <div className="pointer-events-auto relative">
          <button
            type="button"
            onClick={() => setMenuOpen((value) => !value)}
            className="grid h-9 w-9 place-items-center rounded-full text-black/40 hover:bg-black/5 hover:text-black"
            aria-label="工作空间菜单"
          >
            <MoreHorizontal className="h-4 w-4" />
          </button>
          {menuOpen ? (
            <div className="absolute right-0 top-10 z-20 w-28 rounded-2xl border border-black/5 bg-white p-1 text-sm shadow-card">
              <button type="button" onClick={() => { setMenuOpen(false); onRename(); }} className="w-full rounded-xl px-3 py-2 text-left hover:bg-black/5">重命名</button>
              <button type="button" onClick={() => { setMenuOpen(false); onDelete(); }} className="w-full rounded-xl px-3 py-2 text-left text-red-600 hover:bg-red-50">删除</button>
            </div>
          ) : null}
        </div>
      </div>
      <div className="pointer-events-none relative z-0 mt-4">
        <h3 className="truncate text-base font-semibold tracking-tight">{workspace.name}</h3>
        <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-black/45">
          {workspace.description || '持久 Codex 项目工作空间'}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-[11px] text-black/45">
          <span className="inline-flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${runtime.dot}`} />
            {runtime.label}
          </span>
          <span className="inline-flex items-center gap-1"><Server className="h-3 w-3" /> 常驻 runtime</span>
          <span className="inline-flex items-center gap-1"><Clock3 className="h-3 w-3" /> {formatRelative(workspace.updated_at)}</span>
        </div>
      </div>
    </article>
  );
}

function CreateWorkspaceDialog({
  open,
  busy,
  onClose,
  onCreate,
}: {
  open: boolean;
  busy: boolean;
  onClose(): void;
  onCreate(input: WorkspaceCreateInput): Promise<void>;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [kind, setKind] = useState<'empty' | 'git'>('empty');
  const [repositoryUrl, setRepositoryUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [token, setToken] = useState('');

  useEffect(() => {
    if (!open) return;
    setName('');
    setDescription('');
    setKind('empty');
    setRepositoryUrl('');
    setBranch('');
    setToken('');
  }, [open]);

  const disabled = !name.trim() || (kind === 'git' && !repositoryUrl.trim());
  return (
    <AppDialog
      open={open}
      onClose={onClose}
      title="新建工作空间"
      description="每个工作空间拥有独立的持久项目目录、Codex runtime 和多条会话。"
      widthClassName="max-w-lg"
      dismissible={!busy}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy} className="rounded-full border border-black/10 px-4 py-2 text-sm font-medium hover:bg-black/5 disabled:opacity-40">取消</button>
          <button
            type="button"
            disabled={busy || disabled}
            onClick={() => void onCreate({
              name: name.trim(),
              description: description.trim(),
              source: kind === 'empty'
                ? { kind: 'empty' }
                : {
                    kind: 'git',
                    repository_url: repositoryUrl.trim(),
                    default_branch: branch.trim() || undefined,
                    access_token: token || undefined,
                  },
            })}
            className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white hover:bg-black/85 disabled:opacity-40"
          >
            {busy ? '创建中…' : '创建'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="名称"><input value={name} onChange={(event) => setName(event.target.value)} autoFocus placeholder="例如：官网重构" className="h-11 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30" /></Field>
        <Field label="描述（可选）"><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="这个项目要完成什么" className="h-11 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30" /></Field>
        <div>
          <div className="text-xs font-medium text-black/55">开始方式</div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <SourceButton active={kind === 'empty'} onClick={() => setKind('empty')} icon={<FolderKanban className="h-4 w-4" />} title="空目录" detail="稍后上传文件" />
            <SourceButton active={kind === 'git'} onClick={() => setKind('git')} icon={<GitBranch className="h-4 w-4" />} title="私有 Git" detail="HTTPS 仓库" />
          </div>
        </div>
        {kind === 'git' ? (
          <div className="space-y-3 rounded-2xl border border-black/5 bg-black/[0.025] p-4">
            <Field label="HTTPS 仓库地址"><input value={repositoryUrl} onChange={(event) => setRepositoryUrl(event.target.value)} placeholder="https://git.example.com/team/project.git" className="h-11 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30" /></Field>
            <Field label="默认分支（可选）"><input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="main" className="h-11 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30" /></Field>
            <Field label="访问令牌（可选）"><input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" placeholder="只加密保存在此工作空间" className="h-11 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30" /></Field>
            <p className="text-[11px] leading-5 text-black/45">仅支持管理员允许的 HTTPS 主机；令牌不会进入 Codex 容器。</p>
          </div>
        ) : null}
      </div>
    </AppDialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-xs font-medium text-black/55">{label}<div className="mt-2">{children}</div></label>;
}

function SourceButton({ active, onClick, icon, title, detail }: { active: boolean; onClick(): void; icon: React.ReactNode; title: string; detail: string }) {
  return (
    <button type="button" onClick={onClick} className={`rounded-2xl border p-3 text-left transition ${active ? 'border-black bg-black text-white' : 'border-black/10 bg-white hover:border-black/25'}`}>
      <div className="flex items-center gap-2 text-sm font-medium">{icon}{title}</div>
      <div className={`mt-1 text-[11px] ${active ? 'text-white/60' : 'text-black/45'}`}>{detail}</div>
    </button>
  );
}

function runtimeCopy(status: Workspace['runtime_status']) {
  if (status === 'ready') return { label: '已就绪', dot: 'bg-emerald-500' };
  if (status === 'busy') return { label: '运行中', dot: 'bg-blue-500' };
  if (status === 'starting') return { label: '启动中', dot: 'bg-amber-500' };
  if (status === 'error') return { label: '异常', dot: 'bg-red-500' };
  return { label: '未启动', dot: 'bg-black/25' };
}

function formatRelative(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return '刚刚';
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}
